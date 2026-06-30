import os
import io
import csv
import asyncio
import logging
import httpx
import openpyxl
from dotenv import load_dotenv
import db_manager

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{TOKEN}"
FILE_DOWNLOAD_URL = f"https://api.telegram.org/file/bot{TOKEN}"

logger = logging.getLogger(__name__)

# Callback hook to notify FastAPI server (which broadcasts to websockets)
on_status_change = None

async def send_telegram_request(method, payload):
    """Utility to send async requests to Telegram API."""
    if not TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.warning(f"Telegram Bot Token not configured. Skipping API call: {method}")
        return None
        
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(f"{API_URL}/{method}", json=payload, timeout=10.0)
            if response.status_code != 200:
                logger.error(f"Telegram API returned error {response.status_code}: {response.text}")
                return None
            return response.json()
        except Exception as e:
            logger.error(f"Exception calling Telegram API: {e}")
            return None

async def notify_new_cargo(cargo):
    """Sends an alert to all registered analysts on Telegram with inline buttons."""
    chats = db_manager.get_registered_analyst_chats()
    if not chats:
        logger.info("No analysts registered to receive notifications.")
        return

    message = (
        f"🔔 *Nueva Declaración de Carga*\n\n"
        f"📦 *Contenedor:* `{cargo['id']}`\n"
        f"📄 *DUA:* `{cargo['dua']}`\n"
        f"🏢 *Agencia:* {cargo['agency']}\n\n"
        f"¿Autoriza la liberación del despacho?"
    )

    payload = {
        "text": message,
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "SÍ (Liberar) ✓", "callback_data": f"release:{cargo['id']}"},
                    {"text": "NO (Revisión) ⏳", "callback_data": f"reject:{cargo['id']}"}
                ]
            ]
        }
    }

    for chat_id in chats:
        chat_payload = {"chat_id": chat_id, **payload}
        await send_telegram_request("sendMessage", chat_payload)

async def notify_new_cargo_to_chat(chat_id, cargo):
    """Sends an alert card with inline buttons to a specific chat."""
    message = (
        f"🔔 *Declaración Pendiente de Liberación*\n\n"
        f"📦 *Contenedor:* `{cargo['id']}`\n"
        f"📄 *DUA:* `{cargo['dua']}`\n"
        f"🏢 *Agencia:* {cargo['agency']}\n\n"
        f"¿Autoriza la liberación del despacho?"
    )

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "SÍ (Liberar) ✓", "callback_data": f"release:{cargo['id']}"},
                    {"text": "NO (Revisión) ⏳", "callback_data": f"reject:{cargo['id']}"}
                ]
            ]
        }
    }
    await send_telegram_request("sendMessage", payload)

async def handle_callback_query(callback_query):
    """Processes callback clicks from analyst (SI/NO)."""
    callback_id = callback_query.get("id")
    chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
    message_id = callback_query.get("message", {}).get("message_id")
    data = callback_query.get("data", "")
    username = callback_query.get("from", {}).get("username", "Analista")

    if not data or ":" not in data:
        return

    action, container_id = data.split(":", 1)
    
    # Update Database
    new_status = "LIBERADO" if action == "release" else "PENDIENTE"
    updated_cargo = db_manager.update_cargo_status(container_id, new_status)
    
    if not updated_cargo:
        await send_telegram_request("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "Error: La carga no fue encontrada en la base de datos."
        })
        return

    # Trigger WS broadcast in FastAPI
    if on_status_change and updated_cargo:
        await on_status_change(updated_cargo)

    # Respond Callback
    status_text = "Carga LIBERADA" if action == "release" else "Carga RETENIDA en revisión"
    await send_telegram_request("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": f"Acción procesada: {status_text}."
    })

    # Edit original message
    decision_marker = "✓ LIBERADO" if action == "release" else "⏳ RETENIDO EN REVISIÓN"
    original_text = callback_query.get("message", {}).get("text", "")
    
    edited_text = (
        f"{original_text}\n\n"
        f"Resolución: *{decision_marker}*\n"
        f"Procesado por: @{username}"
    )

    await send_telegram_request("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": edited_text,
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": []}
    })

async def handle_document(message):
    """Downloads and processes DUA Excel/CSV files sent by the analyst."""
    chat_id = message["chat"]["id"]
    document = message["document"]
    file_id = document["file_id"]
    file_name = document["file_name"]
    
    # 1. Validate file extension
    ext = file_name.split(".")[-1].lower() if "." in file_name else ""
    if ext not in ["xlsx", "csv"]:
        await send_telegram_request("sendMessage", {
            "chat_id": chat_id,
            "text": "⚠️ *Archivo no soportado.*\n\nPor favor, envía un archivo de Excel (`.xlsx`) o archivo CSV (`.csv`) con el listado de las DUA.",
            "parse_mode": "Markdown"
        })
        return

    # Send loading indicator message
    loading_reply = await send_telegram_request("sendMessage", {
        "chat_id": chat_id,
        "text": "⏳ Descargando y procesando archivo. Por favor espera..."
    })
    loading_msg_id = loading_reply.get("result", {}).get("message_id") if loading_reply else None

    # 2. Get file path from Telegram
    file_info = await send_telegram_request("getFile", {"file_id": file_id})
    if not file_info or not file_info.get("ok"):
        await send_error_message(chat_id, loading_msg_id, "No se pudo obtener información del archivo desde Telegram.")
        return
        
    file_path = file_info["result"]["file_path"]
    download_url = f"{FILE_DOWNLOAD_URL}/{file_path}"

    # 3. Download file bytes
    file_bytes = None
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(download_url, timeout=30.0)
            if response.status_code == 200:
                file_bytes = response.content
            else:
                await send_error_message(chat_id, loading_msg_id, f"Error al descargar archivo: Código {response.status_code}")
                return
        except Exception as e:
            await send_error_message(chat_id, loading_msg_id, f"Excepción de red al descargar archivo: {str(e)}")
            return

    # 4. Parse file based on extension
    cargos_to_upsert = []
    try:
        if ext == "xlsx":
            cargos_to_upsert = parse_xlsx(file_bytes)
        elif ext == "csv":
            cargos_to_upsert = parse_csv(file_bytes)
    except Exception as e:
        await send_error_message(chat_id, loading_msg_id, f"Error al leer el archivo Excel/CSV: {str(e)}")
        return

    if not cargos_to_upsert:
        await send_error_message(chat_id, loading_msg_id, "No se encontraron filas con datos válidos o columnas requeridas (Contenedor/DUA).")
        return

    # 5. Upsert to database and broadcast to Websockets
    total = 0
    liberados = 0
    pendientes = 0
    
    for item in cargos_to_upsert:
        try:
            # db_manager.upsert_cargo handles the 24h filter logic on status updates
            upserted = db_manager.upsert_cargo(
                container_id=item["id"],
                dua_number=item["dua"],
                agency_name=item["agency"],
                status=item["status"]
            )
            if upserted:
                total += 1
                if upserted["status"] == "LIBERADO":
                    liberados += 1
                else:
                    pendientes += 1
                
                # Notify WebSockets about the change live
                if on_status_change:
                    await on_status_change(upserted)
        except Exception as e:
            logger.error(f"Error importing row {item}: {e}")

    # 6. Delete loading message and send final stats message
    if loading_msg_id:
        await send_telegram_request("deleteMessage", {"chat_id": chat_id, "message_id": loading_msg_id})

    final_report = (
        f"📊 *Reporte de Importación de Excel*\n\n"
        f"📄 *Archivo:* `{file_name}`\n"
        f"✅ *Cargas Procesadas:* {total}\n"
        f"🟢 *Liberadas (SÍ):* {liberados}\n"
        f"⏳ *En Revisión (NO):* {pendientes}\n\n"
        f"Las pantallas del puerto han sido actualizadas en tiempo real. 🚀"
    )
    await send_telegram_request("sendMessage", {
        "chat_id": chat_id,
        "text": final_report,
        "parse_mode": "Markdown"
    })

async def send_error_message(chat_id, loading_msg_id, error_text):
    if loading_msg_id:
        await send_telegram_request("deleteMessage", {"chat_id": chat_id, "message_id": loading_msg_id})
    await send_telegram_request("sendMessage", {
        "chat_id": chat_id,
        "text": f"❌ *Error al procesar el archivo:*\n\n{error_text}",
        "parse_mode": "Markdown"
    })

def parse_xlsx(file_bytes):
    """Parses Excel bytes using openpyxl."""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheet = wb.active
    
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []
        
    return process_raw_rows(rows)

def parse_csv(file_bytes):
    """Parses CSV bytes using standard csv module."""
    # Handle UTF-8 with or without BOM
    content = file_bytes.decode("utf-8-sig", errors="ignore")
    
    # Sniff delimiter (semicolon is common in Spanish systems, fallback to comma)
    delimiter = ";"
    if "," in content and content.count(",") > content.count(";"):
        delimiter = ","
        
    reader = csv.reader(io.StringIO(content), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return []
        
    return process_raw_rows(rows)

def process_raw_rows(rows):
    """Helper to detect headers dynamically and extract valid cargos."""
    # Find the header row (first non-empty row)
    header_idx = -1
    for idx, r in enumerate(rows):
        if any(cell is not None and str(cell).strip() != "" for cell in r):
            header_idx = idx
            break
            
    if header_idx == -1:
        return []
        
    headers = [str(cell).strip().lower() if cell is not None else "" for cell in rows[header_idx]]
    
    # Identify indices
    idx_id = -1
    idx_dua = -1
    idx_agency = -1
    idx_status = -1
    
    for i, h in enumerate(headers):
        if "contenedor" in h or "container" in h or "id" in h or "equipo" in h:
            idx_id = i
        elif "dua" in h or "declaracion" in h or "declaración" in h or "numero" in h:
            idx_dua = i
        elif "agencia" in h or "aduana" in h or "transportista" in h:
            idx_agency = i
        elif "estatus" in h or "status" in h or "liberado" in h or "estado" in h or "aprobado" in h:
            idx_status = i

    # Fallback to column order if headers not matching
    if idx_id == -1: idx_id = 0
    if idx_dua == -1 and len(headers) > 1: idx_dua = 1
    if idx_agency == -1 and len(headers) > 2: idx_agency = 2
    if idx_status == -1 and len(headers) > 3: idx_status = 3

    cargos = []
    # Process data rows
    for r in rows[header_idx + 1:]:
        if len(r) <= max(idx_id, idx_dua):
            continue
            
        c_id = str(r[idx_id]).strip().upper() if r[idx_id] is not None else ""
        c_dua = str(r[idx_dua]).strip() if r[idx_dua] is not None else ""
        
        # Validate container ID length (typically 11 characters like MSKU1234567)
        if len(c_id) < 8 or not c_dua:
            continue
            
        c_agency = str(r[idx_agency]).strip() if (idx_agency < len(r) and r[idx_agency] is not None) else "Agencia Desconocida"
        c_status_raw = str(r[idx_status]).strip().lower() if (idx_status < len(r) and r[idx_status] is not None) else "no"
        
        # Translate status
        c_status = "PENDIENTE"
        if c_status_raw in ["si", "sí", "liberado", "liberada", "aprobado", "1", "ok", "true", "yes"]:
            c_status = "LIBERADO"
            
        cargos.append({
            "id": c_id,
            "dua": c_dua,
            "agency": c_agency,
            "status": c_status
        })
        
    return cargos

async def handle_message(message):
    """Processes normal text messages like /start."""
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()
    username = message.get("from", {}).get("username", "Usuario")

    if text == "/start":
        db_manager.register_analyst_chat(chat_id, username)
        welcome_msg = (
            f"👋 *¡Bienvenido al Asistente del Puerto, @{username}!*\n\n"
            f"👮 *Tu Rol:* Analista Operativo del SENIAT.\n\n"
            f"📊 *Función Principal:* Arrastra y envía tu archivo Excel (`.xlsx`) o CSV (`.csv`) con el listado de las DUA directamente a este chat.\n\n"
            f"El bot procesará las cargas automáticamente, actualizará la base de datos y refrescará en tiempo real las pantallas del puerto.\n\n"
            f"💡 *Formato sugerido:* Tu archivo de Excel debe contener las columnas: `Contenedor`, `DUA`, `Agencia` y `Estatus` (donde 'SÍ' o 'Liberado' autoriza la liberación).\n\n"
            f"🟢 *Estatus del Canal:* Listo para recibir archivos de importación."
        )
        await send_telegram_request("sendMessage", {
            "chat_id": chat_id,
            "text": welcome_msg,
            "parse_mode": "Markdown"
        })
    else:
        # Help reply
        await send_telegram_request("sendMessage", {
            "chat_id": chat_id,
            "text": "Envía tu archivo de Excel (.xlsx) o CSV con el listado de las DUA directamente aquí para procesar la importación."
        })

async def bot_polling_loop():
    """Background polling task to receive updates from Telegram."""
    if not TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE" or TOKEN == "8960940221:AAFbNCi2I5Gzj-WJQwNdymdnmGSuDEGzG64":
        # Check if dummy value
        if TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
            logger.warning("Telegram Bot Token is not configured. Bot polling is disabled.")
            return

    logger.info("Starting Telegram Bot Polling Loop...")
    offset = 0
    
    while True:
        try:
            payload = {
                "offset": offset,
                "timeout": 10,
                "allowed_updates": ["message", "callback_query"]
            }
            
            updates = await send_telegram_request("getUpdates", payload)
            
            if updates and updates.get("ok"):
                for update in updates.get("result", []):
                    update_id = update.get("update_id")
                    offset = update_id + 1
                    
                    if "message" in update:
                        msg = update["message"]
                        if "document" in msg:
                            await handle_document(msg)
                        else:
                            await handle_message(msg)
                    elif "callback_query" in update:
                        await handle_callback_query(update["callback_query"])
                        
            await asyncio.sleep(0.5)
            
        except asyncio.CancelledError:
            logger.info("Telegram Bot Polling Loop stopped.")
            break
        except Exception as e:
            logger.error(f"Error in Telegram Bot update loop: {e}")
            await asyncio.sleep(5)
