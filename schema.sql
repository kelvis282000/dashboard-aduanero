-- Drop tables if they exist (for clean initialization)
DROP TABLE IF EXISTS cargos;
DROP TABLE IF EXISTS analyst_chats;

-- Table to store cargos/containers
CREATE TABLE cargos (
    container_id VARCHAR(11) PRIMARY KEY,
    dua_number VARCHAR(20) UNIQUE NOT NULL,
    agency_name VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE', -- 'PENDIENTE' | 'LIBERADO'
    registered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    released_at TIMESTAMP WITH TIME ZONE
);

-- Table to store registered analyst chat IDs from Telegram
CREATE TABLE analyst_chats (
    chat_id BIGINT PRIMARY KEY,
    username VARCHAR(100),
    registered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Insert initial mock cargos (some liberated, some pending)
-- Setting released_at to CURRENT_TIMESTAMP for the active liberated ones
INSERT INTO cargos (container_id, dua_number, agency_name, status, registered_at, released_at) VALUES
('MSKU9845102', '012-2026-0845', 'Aduanas La Guaira C.A.', 'LIBERADO', CURRENT_TIMESTAMP - INTERVAL '1 hour', CURRENT_TIMESTAMP - INTERVAL '1 hour'),
('CMAU6539201', '012-2026-0922', 'Logística Portuaria Nacional', 'LIBERADO', CURRENT_TIMESTAMP - INTERVAL '45 minutes', CURRENT_TIMESTAMP - INTERVAL '45 minutes'),
('SUDU4719283', '012-2026-0955', 'TransMarítima del Caribe', 'PENDIENTE', CURRENT_TIMESTAMP - INTERVAL '30 minutes', NULL),
('MEDU1049283', '012-2026-1011', 'Agencia Aduanal Bolívar', 'PENDIENTE', CURRENT_TIMESTAMP - INTERVAL '20 minutes', NULL),
('ZIMU5019284', '012-2026-1020', 'Aduaservi Express', 'LIBERADO', CURRENT_TIMESTAMP - INTERVAL '10 minutes', CURRENT_TIMESTAMP - INTERVAL '10 minutes'),
('HLXU3348192', '012-2026-1035', 'Aduanas del Puerto C.A.', 'PENDIENTE', CURRENT_TIMESTAMP - INTERVAL '5 minutes', NULL);
