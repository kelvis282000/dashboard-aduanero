/* ==========================================================================
   CUSTOMS CONTROL DASHBOARD - APP LOGIC (Real-time Full-Stack Edition)
   ========================================================================== */

// App State
const state = {
  cargos: [],
  filter: "all", // "all" | "SI" | "NO"
  searchQuery: "",
  botConnected: false,
  autoSimulate: false, // Default to false, can be enabled via Dev Panel
  lastUpdate: Date.now()
};

// Agency options list for auto simulation
const agencyOptions = [
  "Aduanas La Guaira C.A.",
  "Logística Portuaria Nacional",
  "TransMarítima del Caribe",
  "Agencia Aduanal Bolívar",
  "Aduaservi Express",
  "Aduanas del Puerto C.A.",
  "Despachos Rápidos Oriente",
  "Caribe Cargo Logística"
];

// Container prefix options for generator
const containerPrefixes = ["MSKU", "CMAU", "SUDU", "MEDU", "ZIMU", "HLXU", "NYKU", "TRLU"];

let socket = null;
let reconnectTimeout = null;

// Initial setup on document load
document.addEventListener("DOMContentLoaded", () => {
  // Start digital clock
  initClock();
  
  // Start update timer loop
  initUpdateTimer();
  
  // Fetch initial data from REST API
  fetchCargos();

  // Connect to real-time WebSockets
  initWebSocket();

  // Attach search event listener
  const searchInput = document.getElementById("search-input");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      state.searchQuery = e.target.value;
      render();
    });
  }

  // Start automatic TV scrolling
  startAutoScroll();
});

/* ==========================================================================
   API & WebSockets Sync
   ========================================================================== */

// Helper to translate backend DB status to frontend UI status labels
function translateStatus(dbStatus) {
  return dbStatus === "LIBERADO" ? "SI" : "NO";
}

async function fetchCargos() {
  try {
    const response = await fetch("/api/cargos");
    if (!response.ok) throw new Error("HTTP error fetching cargos");
    const data = await response.json();
    
    // Translate statuses
    state.cargos = data.map(item => ({
      ...item,
      status: translateStatus(item.status)
    }));
    
    state.lastUpdate = Date.now();
    render();
  } catch (error) {
    console.error("Error fetching cargos:", error);
    showToast("Error de Conexión", "No se pudo sincronizar los datos de la base de datos.", "error");
  }
}

function initWebSocket() {
  if (socket) {
    socket.close();
  }

  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
  
  console.log("Connecting to WebSocket:", wsUrl);
  socket = new WebSocket(wsUrl);

  socket.onopen = () => {
    console.log("WebSocket connected.");
    state.botConnected = true;
    updateConnectionIndicator(true);
    showToast("Conexión en Vivo", "Conectado al servidor de actualizaciones en tiempo real.", "success");
    if (reconnectTimeout) {
      clearTimeout(reconnectTimeout);
      reconnectTimeout = null;
    }
  };

  socket.onclose = () => {
    console.warn("WebSocket connection closed.");
    state.botConnected = false;
    updateConnectionIndicator(false);
    showToast("Conexión Perdida", "Desconectado del servidor. Reintentando en 5 segundos...", "error");
    
    // Schedule reconnect
    if (!reconnectTimeout) {
      reconnectTimeout = setTimeout(initWebSocket, 5000);
    }
  };

  socket.onerror = (error) => {
    console.error("WebSocket error:", error);
  };

  socket.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      console.log("WebSocket message received:", message);

      if (message.event === "cargo_created") {
        const raw = message.data;
        const newCargo = {
          id: raw.container_id || raw.id,
          dua: raw.dua_number || raw.dua,
          agency: raw.agency_name || raw.agency,
          status: translateStatus(raw.status)
        };
        
        // Trigger 15-second overlay if loaded as LIBERADO
        if (newCargo.status === "SI") {
          showReleaseOverlay(newCargo);
        }
        
        // Add to local state list (unshift since API returns sorted DESC)
        state.cargos.unshift(newCargo);
        state.lastUpdate = Date.now();
        render();
        showToast("Carga Registrada", `Contenedor ${newCargo.id} ingresado por la agencia.`, "info");
      } 
      else if (message.event === "cargo_updated") {
        const raw = message.data;
        const updatedCargo = {
          id: raw.container_id || raw.id,
          dua: raw.dua_number || raw.dua,
          agency: raw.agency_name || raw.agency,
          status: raw.status
        };
        
        const index = state.cargos.findIndex(c => c.id === updatedCargo.id);
        const newStatus = translateStatus(updatedCargo.status);
        
        // Trigger overlay if the container is liberated (SI) and it wasn't already marked as SI in our local state
        const isAlreadyLiberated = index !== -1 && state.cargos[index].status === "SI";
        if (newStatus === "SI" && !isAlreadyLiberated) {
          showReleaseOverlay(updatedCargo);
        }
        
        if (index !== -1) {
          state.cargos[index].status = newStatus;
          state.lastUpdate = Date.now();
          render();
          
          const title = updatedCargo.status === "LIBERADO" ? "DUA Liberado (SÍ)" : "DUA en Revisión (NO)";
          const type = updatedCargo.status === "LIBERADO" ? "success" : "error";
          showToast(title, `Estatus del contenedor ${updatedCargo.id} actualizado.`, type);
        } else {
          // If we don't have it, add it to the state list and render
          state.cargos.unshift({
            id: updatedCargo.id,
            dua: updatedCargo.dua,
            agency: updatedCargo.agency,
            status: newStatus,
            time: raw.time || new Date().toLocaleTimeString("es-VE", { hour12: false })
          });
          state.lastUpdate = Date.now();
          render();
        }
      }
    } catch (e) {
      console.error("Error parsing WS message:", e);
    }
  };
}

function updateConnectionIndicator(isOnline) {
  const indicator = document.getElementById("bot-status-indicator");
  const statusText = document.getElementById("bot-status-text");

  if (isOnline) {
    if (indicator) {
      indicator.className = "connection-indicator online";
    }
    if (statusText) {
      statusText.textContent = "Servicio de Telegram Bot: En línea";
    }
  } else {
    if (indicator) {
      indicator.className = "connection-indicator offline";
    }
    if (statusText) {
      statusText.textContent = "Servicio de Telegram Bot: Desconectado";
    }
  }
}

/* ==========================================================================
   Clock & Time Functions
   ========================================================================== */
function initClock() {
  const clockElement = document.getElementById("digital-clock");
  if (!clockElement) return;

  function updateClock() {
    const now = new Date();
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    const seconds = String(now.getSeconds()).padStart(2, '0');
    clockElement.textContent = `${hours}:${minutes}:${seconds}`;
  }

  updateClock();
  setInterval(updateClock, 1000);
}

function initUpdateTimer() {
  const updateText = document.getElementById("last-update-text");
  if (!updateText) return;

  function updateRelativeTime() {
    const diffSeconds = Math.floor((Date.now() - state.lastUpdate) / 1000);

    if (diffSeconds < 5) {
      updateText.textContent = "Actualizado hace un momento";
    } else if (diffSeconds < 60) {
      updateText.textContent = `Actualizado hace ${diffSeconds} segundos`;
    } else {
      const minutes = Math.floor(diffSeconds / 60);
      updateText.textContent = `Actualizado hace ${minutes} min`;
    }
  }

  updateRelativeTime();
  setInterval(updateRelativeTime, 5000);
}

/* ==========================================================================
   Render & UI Updates
   ========================================================================== */
function render() {
  const filteredCargos = getFilteredCargos();
  renderTable(filteredCargos);
  renderMobileCards(filteredCargos);
  updateKPIs();
}

function getFilteredCargos() {
  return state.cargos.filter(cargo => {
    if (state.filter !== "all" && cargo.status !== state.filter) {
      return false;
    }
    
    if (state.searchQuery.trim() !== "") {
      const query = state.searchQuery.toLowerCase();
      const matchId = cargo.id.toLowerCase().includes(query);
      const matchDua = cargo.dua.toLowerCase().includes(query);
      const matchAgency = cargo.agency.toLowerCase().includes(query);
      return matchId || matchDua || matchAgency;
    }
    
    return true;
  });
}

function updateKPIs() {
  const kpiTotal = document.getElementById("kpi-total");
  const kpiLiberados = document.getElementById("kpi-liberados");
  const kpiRevision = document.getElementById("kpi-revision");

  const total = state.cargos.length;
  const liberados = state.cargos.filter(c => c.status === "SI").length;
  const revision = state.cargos.filter(c => c.status === "NO").length;

  if (kpiTotal) kpiTotal.textContent = String(total);
  if (kpiLiberados) kpiLiberados.textContent = String(liberados);
  if (kpiRevision) kpiRevision.textContent = String(revision);

  const kpiTotalSub = document.getElementById("kpi-total-sub");
  const kpiLiberadosSub = document.getElementById("kpi-liberados-sub");
  const kpiRevisionSub = document.getElementById("kpi-revision-sub");

  if (kpiTotalSub) kpiTotalSub.textContent = `${total} cargas registradas hoy`;
  if (kpiLiberadosSub) {
    const percentage = total > 0 ? Math.round((liberados / total) * 100) : 0;
    kpiLiberadosSub.textContent = `${percentage}% del despacho completado`;
  }
  if (kpiRevisionSub) {
    kpiRevisionSub.textContent = `${revision} cargas en cola de revisión`;
  }
}

function renderTable(cargos) {
  const tableBody = document.getElementById("cargo-table-body");
  if (!tableBody) return;
  
  tableBody.innerHTML = "";

  if (cargos.length === 0) {
    const emptyRow = document.createElement("tr");
    const emptyCell = document.createElement("td");
    emptyCell.setAttribute("colspan", "5");
    
    const emptyDiv = document.createElement("div");
    emptyDiv.className = "empty-state";
    emptyDiv.innerHTML = `
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <line x1="8" y1="12" x2="16" y2="12"/>
      </svg>
      <h3>No se encontraron registros</h3>
      <p>Prueba ajustando los filtros o realizando otra búsqueda.</p>
    `;
    emptyCell.appendChild(emptyDiv);
    emptyRow.appendChild(emptyCell);
    tableBody.appendChild(emptyRow);
    return;
  }

  cargos.forEach(cargo => {
    const row = document.createElement("tr");
    
    const tdId = document.createElement("td");
    tdId.className = "table-cell-bold";
    tdId.textContent = cargo.id;
    row.appendChild(tdId);

    const tdDua = document.createElement("td");
    tdDua.textContent = cargo.dua;
    row.appendChild(tdDua);

    const tdAgency = document.createElement("td");
    tdAgency.textContent = cargo.agency;
    row.appendChild(tdAgency);

    const tdTime = document.createElement("td");
    tdTime.className = "table-cell-time";
    tdTime.textContent = cargo.time;
    row.appendChild(tdTime);

    const tdStatus = document.createElement("td");
    const badge = createStatusBadge(cargo.status);
    tdStatus.appendChild(badge);
    row.appendChild(tdStatus);

    tableBody.appendChild(row);
  });
}

function renderMobileCards(cargos) {
  const cardsList = document.getElementById("mobile-cards-list");
  if (!cardsList) return;
  
  cardsList.innerHTML = "";

  if (cargos.length === 0) {
    const emptyDiv = document.createElement("div");
    emptyDiv.className = "empty-state";
    emptyDiv.innerHTML = `
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <line x1="8" y1="12" x2="16" y2="12"/>
      </svg>
      <h3>No se encontraron registros</h3>
    `;
    cardsList.appendChild(emptyDiv);
    return;
  }

  cargos.forEach(cargo => {
    const card = document.createElement("div");
    card.className = "mobile-card";

    const cardHeader = document.createElement("div");
    cardHeader.className = "mobile-card-header";
    
    const cardTitle = document.createElement("span");
    cardTitle.className = "mobile-card-title";
    cardTitle.textContent = cargo.id;
    
    const cardTime = document.createElement("span");
    cardTime.className = "mobile-card-time";
    cardTime.textContent = cargo.time;

    cardHeader.appendChild(cardTitle);
    cardHeader.appendChild(cardTime);
    card.appendChild(cardHeader);

    const rowDua = document.createElement("div");
    rowDua.className = "mobile-card-row";
    const labelDua = document.createElement("span");
    labelDua.className = "mobile-card-label";
    labelDua.textContent = "DUA:";
    const valDua = document.createElement("span");
    valDua.className = "mobile-card-value";
    valDua.textContent = cargo.dua;
    rowDua.appendChild(labelDua);
    rowDua.appendChild(valDua);
    card.appendChild(rowDua);

    const rowAgency = document.createElement("div");
    rowAgency.className = "mobile-card-row";
    const labelAgency = document.createElement("span");
    labelAgency.className = "mobile-card-label";
    labelAgency.textContent = "Agencia:";
    const valAgency = document.createElement("span");
    valAgency.className = "mobile-card-value";
    valAgency.textContent = cargo.agency;
    rowAgency.appendChild(labelAgency);
    rowAgency.appendChild(valAgency);
    card.appendChild(rowAgency);

    const rowStatus = document.createElement("div");
    rowStatus.className = "mobile-card-row";
    const labelStatus = document.createElement("span");
    labelStatus.className = "mobile-card-label";
    labelStatus.textContent = "Estatus SENIAT:";
    const valStatus = createStatusBadge(cargo.status);
    rowStatus.appendChild(labelStatus);
    rowStatus.appendChild(valStatus);
    card.appendChild(rowStatus);

    cardsList.appendChild(card);
  });
}

function createStatusBadge(status) {
  const badge = document.createElement("span");
  badge.className = `status-badge ${status === "SI" ? "liberado" : "pendiente"}`;

  const iconSpan = document.createElement("span");
  iconSpan.className = "status-icon";

  const textSpan = document.createElement("span");

  if (status === "SI") {
    iconSpan.textContent = "✓";
    textSpan.textContent = "Liberado";
  } else {
    iconSpan.textContent = "⏳";
    textSpan.textContent = "Pendiente";
  }

  badge.appendChild(iconSpan);
  badge.appendChild(textSpan);
  return badge;
}

function setFilter(filterType) {
  state.filter = filterType;

  const btnAll = document.getElementById("filter-btn-all");
  const btnSi = document.getElementById("filter-btn-si");
  const btnNo = document.getElementById("filter-btn-no");

  if (btnAll) btnAll.classList.remove("active");
  if (btnSi) btnSi.classList.remove("active");
  if (btnNo) btnNo.classList.remove("active");

  const activeBtn = document.getElementById(`filter-btn-${filterType.toLowerCase()}`);
  if (activeBtn) activeBtn.classList.add("active");

  render();
}

/* ==========================================================================
   Toast Notification System
   ========================================================================== */
function showToast(title, body, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast ${type}`;

  const header = document.createElement("div");
  header.className = "toast-header";
  
  const icon = document.createElement("span");
  if (type === "success") icon.textContent = "✓";
  else if (type === "error") icon.textContent = "✗";
  else icon.textContent = "ℹ";

  const titleText = document.createElement("span");
  titleText.textContent = title;

  header.appendChild(icon);
  header.appendChild(titleText);
  toast.appendChild(header);

  const bodyText = document.createElement("div");
  bodyText.className = "toast-body";
  bodyText.textContent = body;
  toast.appendChild(bodyText);

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.animation = "slide-in-left 0.3s cubic-bezier(0.16, 1, 0.3, 1) reverse forwards";
    setTimeout(() => {
      if (toast.parentNode === container) {
        container.removeChild(toast);
      }
    }, 300);
  }, 4000);
}

/* ==========================================================================
   TV Auto-Scroll & 15s Highlight Release Overlay
   ========================================================================== */
let autoScrollInterval = null;

function startAutoScroll() {
  if (autoScrollInterval) clearInterval(autoScrollInterval);
  
  const container = document.getElementById("table-container");
  if (!container) return;
  
  autoScrollInterval = setInterval(() => {
    // Only scroll if contents exceed visible height
    if (container.scrollHeight > container.clientHeight) {
      container.scrollTop += 1;
      
      // Reached bottom
      if (container.scrollTop + container.clientHeight >= container.scrollHeight - 1) {
        clearInterval(autoScrollInterval);
        setTimeout(() => {
          // Smooth scroll back to top
          container.scrollTo({ top: 0, behavior: "smooth" });
          setTimeout(startAutoScroll, 2000); // Resume scrolling after 2s
        }, 3000); // Hold at bottom for 3s
      }
    }
  }, 40); // Smooth scroll velocity
}

let overlayTimeout = null;
const releaseQueue = [];
let overlayActive = false;

function showReleaseOverlay(cargo) {
  releaseQueue.push(cargo);
  processReleaseQueue();
}

function processReleaseQueue() {
  if (overlayActive || releaseQueue.length === 0) return;
  
  overlayActive = true;
  const cargo = releaseQueue.shift();
  
  const overlay = document.getElementById("release-overlay");
  const containerIdEl = document.getElementById("overlay-container-id");
  const duaEl = document.getElementById("overlay-dua-number");
  const agencyEl = document.getElementById("overlay-agency-name");
  const progressEl = document.getElementById("overlay-progress");
  
  if (!overlay || !containerIdEl || !duaEl || !agencyEl || !progressEl) {
    overlayActive = false;
    return;
  }
  
  // Set text content
  containerIdEl.textContent = cargo.id;
  duaEl.textContent = cargo.dua;
  agencyEl.textContent = cargo.agency;
  
  // Reset progress bar layout
  progressEl.style.transition = "none";
  progressEl.style.width = "100%";
  
  // Show overlay
  overlay.classList.remove("hidden");
  // Force repaint to register style transition resets
  void overlay.offsetWidth;
  overlay.classList.add("visible");
  
  // Start progress bar shrink animation (7 seconds)
  setTimeout(() => {
    progressEl.style.transition = "width 7s linear";
    progressEl.style.width = "0%";
  }, 100);
  
  // Auto-hide overlay after 7 seconds
  overlayTimeout = setTimeout(() => {
    overlay.classList.remove("visible");
    setTimeout(() => {
      overlay.classList.add("hidden");
      overlayActive = false;
      // Process next cargo in the queue
      processReleaseQueue();
    }, 400); // Wait for CSS opacity fade out
  }, 7000);
}
