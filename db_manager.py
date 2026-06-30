import os
import logging
import datetime
import threading
from psycopg2 import pool
from dotenv import load_dotenv

# Load env variables
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL variable not set in environment or .env file.")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Fallback In-Memory State
IN_MEMORY_MODE = False
in_memory_cargos = {}  # container_id -> dict
in_memory_chats = set()
in_memory_lock = threading.Lock()

# Verify database exists, create if not
def create_database_if_not_exists():
    if not DATABASE_URL:
        return
    try:
        import urllib.parse
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        
        url = urllib.parse.urlparse(DATABASE_URL)
        db_name = url.path.lstrip('/')
        
        # Build default DSN to connect to default 'postgres' database
        default_dsn = DATABASE_URL.rsplit('/', 1)[0] + "/postgres"
        
        conn = psycopg2.connect(dsn=default_dsn)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT 1 FROM pg_database WHERE datname='{db_name}'")
            exists = cursor.fetchone()
            if not exists:
                logger.info(f"Database '{db_name}' does not exist. Creating dynamically...")
                cursor.execute(f"CREATE DATABASE {db_name}")
                logger.info(f"Database '{db_name}' created successfully.")
        conn.close()
    except Exception as e:
        logger.warning(f"Could not verify or create database automatically: {e}. Relying on existing setup.")

create_database_if_not_exists()

# Initialize connection pool
try:
    connection_pool = pool.SimpleConnectionPool(1, 10, dsn=DATABASE_URL)
    logger.info("PostgreSQL connection pool initialized successfully.")
except Exception as e:
    logger.warning(f"Error initializing PostgreSQL connection pool: {e}. Switching to IN-MEMORY fallback mode.")
    connection_pool = None
    IN_MEMORY_MODE = True

def get_connection():
    if connection_pool:
        return connection_pool.getconn()
    raise ConnectionError("Database connection pool is not available.")

def release_connection(conn):
    if connection_pool and conn:
        connection_pool.putconn(conn)

def init_db():
    """Initializes the database schema if tables do not exist."""
    global IN_MEMORY_MODE
    if IN_MEMORY_MODE:
        logger.info("Initializing in-memory database with mock data...")
        with in_memory_lock:
            now = datetime.datetime.now()
            mock_list = [
                ('MSKU9845102', '012-2026-0845', 'Aduanas La Guaira C.A.', 'LIBERADO', now - datetime.timedelta(hours=1)),
                ('CMAU6539201', '012-2026-0922', 'Logística Portuaria Nacional', 'LIBERADO', now - datetime.timedelta(minutes=45)),
                ('SUDU4719283', '012-2026-0955', 'TransMarítima del Caribe', 'PENDIENTE', now - datetime.timedelta(minutes=30)),
                ('MEDU1049283', '012-2026-1011', 'Agencia Aduanal Bolívar', 'PENDIENTE', now - datetime.timedelta(minutes=20)),
                ('ZIMU5019284', '012-2026-1020', 'Aduaservi Express', 'LIBERADO', now - datetime.timedelta(minutes=10)),
                ('HLXU3348192', '012-2026-1035', 'Aduanas del Puerto C.A.', 'PENDIENTE', now - datetime.timedelta(minutes=5))
            ]
            for item in mock_list:
                released = item[4] if item[3] == 'LIBERADO' else None
                in_memory_cargos[item[0]] = {
                    "id": item[0],
                    "dua": item[1],
                    "agency": item[2],
                    "status": item[3],
                    "registered_at": item[4],
                    "released_at": released
                }
        logger.info("In-memory database initialized successfully.")
        return

    logger.info("Initializing database...")
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            # Check if tables exist, if not run schema creation
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'cargos'
                );
            """)
            tables_exist = cursor.fetchone()[0]
            
            if not tables_exist:
                logger.info("Tables not found. Running schema creation...")
                schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
                if os.path.exists(schema_path):
                    with open(schema_path, "r", encoding="utf-8") as f:
                        schema_sql = f.read()
                    cursor.execute(schema_sql)
                    conn.commit()
                    logger.info("Schema initialized successfully and mock data inserted.")
                else:
                    logger.warning("schema.sql file not found. Database setup skipped.")
            else:
                logger.info("Database tables already exist. Skipping schema setup.")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            release_connection(conn)

def get_all_cargos():
    """Fetches all cargos ordered by registration time descending. Excludes LIBERADO > 24 hours."""
    if IN_MEMORY_MODE:
        with in_memory_lock:
            now = datetime.datetime.now()
            result = []
            for cargo in in_memory_cargos.values():
                is_liberado = cargo["status"] == "LIBERADO"
                released_at = cargo["released_at"]
                if not is_liberado or (released_at and now - released_at < datetime.timedelta(hours=24)):
                    result.append({
                        "id": cargo["id"],
                        "dua": cargo["dua"],
                        "agency": cargo["agency"],
                        "status": cargo["status"],
                        "time": cargo["registered_at"].strftime("%H:%M:%S")
                    })
            sorted_result = sorted(
                result, 
                key=lambda x: in_memory_cargos[x["id"]]["registered_at"], 
                reverse=True
            )
            return sorted_result

    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT container_id, dua_number, agency_name, status, 
                       to_char(registered_at, 'HH24:MI:SS') as time_str 
                FROM cargos 
                WHERE status != 'LIBERADO' OR released_at >= NOW() - INTERVAL '24 hours'
                ORDER BY registered_at DESC;
            """)
            rows = cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "dua": row[1],
                    "agency": row[2],
                    "status": row[3],
                    "time": row[4]
                } for row in rows
            ]
    except Exception as e:
        logger.error(f"Error fetching cargos: {e}")
        return []
    finally:
        if conn:
            release_connection(conn)

def create_cargo(container_id, dua_number, agency_name, status="PENDIENTE"):
    """Inserts a new cargo into the database."""
    if IN_MEMORY_MODE:
        with in_memory_lock:
            now = datetime.datetime.now()
            released = now if status == "LIBERADO" else None
            cargo = {
                "id": container_id,
                "dua": dua_number,
                "agency": agency_name,
                "status": status,
                "registered_at": now,
                "released_at": released
            }
            in_memory_cargos[container_id] = cargo
            return {
                "id": container_id,
                "dua": dua_number,
                "agency": agency_name,
                "status": status,
                "time": now.strftime("%H:%M:%S")
            }

    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO cargos (container_id, dua_number, agency_name, status, released_at)
                VALUES (%s, %s, %s, %s, CASE WHEN %s = 'LIBERADO' THEN CURRENT_TIMESTAMP ELSE NULL END)
                RETURNING container_id, dua_number, agency_name, status, 
                          to_char(registered_at, 'HH24:MI:SS') as time_str;
            """, (container_id, dua_number, agency_name, status, status))
            row = cursor.fetchone()
            conn.commit()
            return {
                "id": row[0],
                "dua": row[1],
                "agency": row[2],
                "status": row[3],
                "time": row[4]
            }
    except Exception as e:
        logger.error(f"Error creating cargo: {e}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            release_connection(conn)

def update_cargo_status(container_id, status):
    """Updates status and sets released_at timestamp if liberated."""
    if IN_MEMORY_MODE:
        with in_memory_lock:
            if container_id in in_memory_cargos:
                now = datetime.datetime.now()
                cargo = in_memory_cargos[container_id]
                cargo["status"] = status
                if status == "LIBERADO":
                    cargo["released_at"] = now
                else:
                    cargo["released_at"] = None
                return {
                    "id": cargo["id"],
                    "dua": cargo["dua"],
                    "agency": cargo["agency"],
                    "status": cargo["status"],
                    "time": cargo["registered_at"].strftime("%H:%M:%S")
                }
            return None

    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE cargos 
                SET status = %s,
                    released_at = CASE WHEN %s = 'LIBERADO' THEN CURRENT_TIMESTAMP ELSE NULL END
                WHERE container_id = %s
                RETURNING container_id, dua_number, agency_name, status, 
                          to_char(registered_at, 'HH24:MI:SS') as time_str;
            """, (status, status, container_id))
            row = cursor.fetchone()
            conn.commit()
            if row:
                return {
                    "id": row[0],
                    "dua": row[1],
                    "agency": row[2],
                    "status": row[3],
                    "time": row[4]
                }
            return None
    except Exception as e:
        logger.error(f"Error updating cargo status: {e}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            release_connection(conn)

def upsert_cargo(container_id, dua_number, agency_name, status):
    """Inserts or updates a cargo. Protects previously liberated cargo timestamps."""
    if IN_MEMORY_MODE:
        with in_memory_lock:
            now = datetime.datetime.now()
            exists = container_id in in_memory_cargos
            if exists:
                existing = in_memory_cargos[container_id]
                if existing["status"] == "LIBERADO":
                    status_to_set = "LIBERADO"
                    released_at_to_set = existing["released_at"]
                else:
                    status_to_set = status
                    released_at_to_set = now if status == "LIBERADO" else None
                    
                in_memory_cargos[container_id] = {
                    "id": container_id,
                    "dua": dua_number,
                    "agency": agency_name,
                    "status": status_to_set,
                    "registered_at": existing["registered_at"],
                    "released_at": released_at_to_set
                }
            else:
                released_at_to_set = now if status == "LIBERADO" else None
                in_memory_cargos[container_id] = {
                    "id": container_id,
                    "dua": dua_number,
                    "agency": agency_name,
                    "status": status,
                    "registered_at": now,
                    "released_at": released_at_to_set
                }
            cargo = in_memory_cargos[container_id]
            return {
                "id": cargo["id"],
                "dua": cargo["dua"],
                "agency": cargo["agency"],
                "status": cargo["status"],
                "time": cargo["registered_at"].strftime("%H:%M:%S")
            }

    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO cargos (container_id, dua_number, agency_name, status, released_at)
                VALUES (%s, %s, %s, %s, CASE WHEN %s = 'LIBERADO' THEN CURRENT_TIMESTAMP ELSE NULL END)
                ON CONFLICT (container_id) 
                DO UPDATE SET 
                  dua_number = EXCLUDED.dua_number,
                  agency_name = EXCLUDED.agency_name,
                  status = CASE 
                             WHEN cargos.status = 'LIBERADO' THEN 'LIBERADO' 
                             ELSE EXCLUDED.status 
                           END,
                  released_at = CASE 
                                  WHEN cargos.status = 'LIBERADO' THEN cargos.released_at 
                                  WHEN EXCLUDED.status = 'LIBERADO' THEN CURRENT_TIMESTAMP 
                                  ELSE NULL 
                                END
                RETURNING container_id, dua_number, agency_name, status, 
                          to_char(registered_at, 'HH24:MI:SS') as time_str;
            """, (container_id, dua_number, agency_name, status, status))
            row = cursor.fetchone()
            conn.commit()
            if row:
                return {
                    "id": row[0],
                    "dua": row[1],
                    "agency": row[2],
                    "status": row[3],
                    "time": row[4]
                }
            return None
    except Exception as e:
        logger.error(f"Error upserting cargo: {e}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            release_connection(conn)

def register_analyst_chat(chat_id, username):
    """Registers an analyst chat ID to receive alerts."""
    if IN_MEMORY_MODE:
        with in_memory_lock:
            in_memory_chats.add(chat_id)
            logger.info(f"Registered in-memory analyst chat ID: {chat_id} (@{username})")
            return

    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO analyst_chats (chat_id, username)
                VALUES (%s, %s)
                ON CONFLICT (chat_id) DO UPDATE 
                SET username = EXCLUDED.username;
            """, (chat_id, username))
            conn.commit()
            logger.info(f"Registered analyst chat ID: {chat_id} (@{username})")
    except Exception as e:
        logger.error(f"Error registering analyst chat ID: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            release_connection(conn)

def get_registered_analyst_chats():
    """Fetches all registered chat IDs of analysts."""
    if IN_MEMORY_MODE:
        with in_memory_lock:
            return list(in_memory_chats)

    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT chat_id FROM analyst_chats;")
            rows = cursor.fetchall()
            return [row[0] for row in rows]
    except Exception as e:
        logger.error(f"Error fetching analyst chats: {e}")
        return []
    finally:
        if conn:
            release_connection(conn)
