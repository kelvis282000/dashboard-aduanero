import os
import asyncio
import logging
import csv
import io
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

import db_manager
import telegram_bot

# Load env variables
load_dotenv()
PORT = int(os.getenv("PORT", 8000))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI Application Setup
app = FastAPI(title="Panel de Control Aduanero API")

# Store background tasks
background_tasks = []

# Pydantic schemas
class CargoCreate(BaseModel):
  id: str
  dua: str
  agency: str
  status: str = "PENDIENTE"

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"New client connected to WebSockets. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"Client disconnected from WebSockets. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcasts a JSON message to all connected clients."""
        logger.info(f"Broadcasting websocket update to {len(self.active_connections)} clients...")
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending WebSocket message to client: {e}")
                # We do not disconnect here, it will be handled by disconnect on block closure

manager = ConnectionManager()

# Link the bot callback to the websocket broadcast
async def on_cargo_status_change(updated_cargo):
    """Callback triggered when the bot updates a cargo status."""
    message = {
        "event": "cargo_updated",
        "data": updated_cargo
    }
    await manager.broadcast(message)

telegram_bot.on_status_change = on_cargo_status_change

# Startup & Shutdown events
@app.on_event("startup")
async def startup_event():
    # 1. Initialize PostgreSQL tables & mock data
    db_manager.init_db()

    # 2. Launch Telegram Bot Polling Loop in the background
    bot_task = asyncio.create_task(telegram_bot.bot_polling_loop())
    background_tasks.append(bot_task)
    logger.info("FastAPI startup: Database checked and Telegram Bot polling launched.")

@app.on_event("shutdown")
async def shutdown_event():
    # Cancel all background loops
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
    logger.info("FastAPI shutdown: Background polling tasks stopped.")

# REST API Endpoints
@app.get("/api/cargos")
def get_cargos():
    """Fetches all cargos currently registered."""
    return db_manager.get_all_cargos()

@app.get("/api/cargos/export")
def export_cargos_excel():
    """Generates an Excel-compatible CSV file containing the cargo logs."""
    cargos = db_manager.get_all_cargos()
    
    # Create memory buffer
    output = io.StringIO()
    # Excel UTF-8 BOM so it opens correctly with accents on Spanish OS
    output.write('\ufeff')
    
    writer = csv.writer(output, delimiter=';')
    # Headers
    writer.writerow(["ID Contenedor", "Número DUA", "Agencia de Aduana", "Estatus SENIAT", "Hora de Ingreso"])
    
    # Rows
    for cargo in cargos:
        status_label = "LIBERADO" if cargo["status"] == "LIBERADO" else "PENDIENTE"
        writer.writerow([cargo["id"], cargo["dua"], cargo["agency"], status_label, cargo["time"]])
        
    return Response(
        content=output.getvalue().encode('utf-8'),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=reporte_despachos_dua.csv",
            "Cache-Control": "no-cache"
        }
    )

@app.post("/api/cargos")
async def add_cargo(cargo: CargoCreate):
    """Registers a new cargo, alerts analysts on Telegram, and broadcasts via WebSockets."""
    try:
        # Save in database
        status_value = cargo.status.upper()
        if status_value not in ["SI", "NO", "LIBERADO", "PENDIENTE"]:
            status_value = "PENDIENTE"
        
        # Translate frontend labels (SI/NO) to database labels (LIBERADO/PENDIENTE)
        db_status = "LIBERADO" if status_value in ["SI", "LIBERADO"] else "PENDIENTE"

        new_cargo = db_manager.create_cargo(
            container_id=cargo.id.upper(),
            dua_number=cargo.dua,
            agency_name=cargo.agency,
            status=db_status
        )

        # Notify via WebSockets about new registration
        ws_msg = {
            "event": "cargo_created",
            "data": new_cargo
        }
        await manager.broadcast(ws_msg)

        # Notify Telegram Bot Analysts (Async)
        # We fire and forget this so API response is immediate
        asyncio.create_task(telegram_bot.notify_new_cargo(new_cargo))

        return new_cargo
    except Exception as e:
        logger.error(f"Error in POST /api/cargos: {e}")
        raise HTTPException(status_code=400, detail=f"Database insertion failed: {str(e)}")

@app.post("/api/dev/simulate-release")
async def simulate_release():
    """Simulates a Telegram bot release action by updating the oldest pending cargo."""
    cargos = db_manager.get_all_cargos()
    pending = [c for c in cargos if c["status"] == "PENDIENTE"]
    if not pending:
        raise HTTPException(status_code=400, detail="No pending cargos found.")
    
    # Oldest pending cargo (last one in list ordered DESC by registered_at)
    oldest = pending[-1]
    
    updated = db_manager.update_cargo_status(oldest["id"], "LIBERADO")
    if updated:
        await on_cargo_status_change(updated)
        return updated
    raise HTTPException(status_code=500, detail="Failed to update cargo status.")

# WebSocket Connection Handler
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Just keep connection open, receiving heartbeats if needed
            # We don't expect messages from dashboard clients, only send updates.
            data = await websocket.receive_text()
            # Send simple ping-pong
            await websocket.send_json({"event": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")
        manager.disconnect(websocket)

# Mount Frontend static files directory
# Note: Keep this last, so it doesn't shadow the api/ws routes
public_dir = os.path.join(os.path.dirname(__file__), "public")
if os.path.exists(public_dir):
    app.mount("/", StaticFiles(directory=public_dir, html=True), name="static")
    logger.info("Serving public/ directory static files at root /")
else:
    logger.warning("public/ directory not found. Static files server skipped.")

if __name__ == "__main__":
    import uvicorn
    # Start server
    logger.info(f"Starting server on port {PORT}...")
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
