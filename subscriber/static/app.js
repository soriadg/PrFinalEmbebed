const compartmentGrid = document.getElementById("compartmentGrid");
const cardTemplate = document.getElementById("cardTemplate");
const openCount = document.getElementById("openCount");
const publisherStatus = document.getElementById("publisherStatus");
const monitoringStatus = document.getElementById("monitoringStatus");
const alarmBanner = document.getElementById("alarmBanner");
const alarmMessage = document.getElementById("alarmMessage");
const eventList = document.getElementById("eventList");
const ackButton = document.getElementById("ackButton");
const syncButton = document.getElementById("syncButton");
const monitorButton = document.getElementById("monitorButton");

let currentState = null;

function formatDate(value) {
  if (!value) {
    return "Sin registro";
  }
  return new Date(value).toLocaleString("es-MX");
}

function eventLabel(type) {
  const labels = {
    publisher_online: "Publisher en línea",
    publisher_offline: "Publisher fuera de línea",
    compartment_opened: "Compartimento abierto",
    compartment_closed: "Compartimento cerrado",
    alarm_raised: "Alarma activada",
    alarm_cleared: "Alarma liberada",
    alarm_acknowledged: "Alarma reconocida",
    monitoring_enabled: "Monitoreo activado",
    monitoring_disabled: "Monitoreo desactivado",
  };
  return labels[type] || type;
}

function renderCompartment(cardState) {
  const node = cardTemplate.content.firstElementChild.cloneNode(true);
  node.classList.toggle("open", cardState.is_open);
  node.querySelector(".label").textContent = cardState.name;
  node.querySelector(".badge").textContent = cardState.is_open ? "Abierto" : "Cerrado";
  node.querySelector(".state").textContent = cardState.is_open
    ? "Se detectó apertura"
    : "Estado normal";
  node.querySelector(".openings").textContent = cardState.total_openings;
  node.querySelector(".lastChange").textContent = formatDate(cardState.last_change_at);
  return node;
}

function renderEvents(events) {
  eventList.innerHTML = "";
  if (!events.length) {
    eventList.innerHTML = '<p class="empty">Aún no hay eventos reportados.</p>';
    return;
  }

  events.forEach((event) => {
    const item = document.createElement("article");
    item.className = "event-item";
    item.innerHTML = `
      <strong>${eventLabel(event.type)}</strong>
      <p>${JSON.stringify(event)}</p>
      <span>${formatDate(event.timestamp)}</span>
    `;
    eventList.appendChild(item);
  });
}

function renderState(state) {
  currentState = state;
  compartmentGrid.innerHTML = "";
  state.compartments.forEach((item) => {
    compartmentGrid.appendChild(renderCompartment(item));
  });

  openCount.textContent = state.open_count;
  publisherStatus.textContent = state.publisher_online ? "Publisher en línea" : "Publisher desconectado";
  publisherStatus.classList.toggle("ok", state.publisher_online);
  monitoringStatus.textContent = state.monitoring_enabled ? "Monitoreo activo" : "Monitoreo desactivado";
  monitoringStatus.classList.toggle("warn", !state.monitoring_enabled);
  monitorButton.textContent = state.monitoring_enabled ? "Desactivar monitoreo" : "Activar monitoreo";

  alarmBanner.classList.toggle("active", state.alarm_active);
  if (state.alarm_active) {
    alarmBanner.querySelector("strong").textContent = "Alarma activa";
    alarmMessage.textContent = `Compartimentos abiertos: ${state.open_compartments.join(", ")}`;
  } else {
    alarmBanner.querySelector("strong").textContent = "Sin alarmas activas";
    alarmMessage.textContent = "Todos los compartimentos reportan estado cerrado.";
  }

  renderEvents(state.recent_events);
}

async function postJson(url, payload = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return response.json();
}

ackButton.addEventListener("click", async () => {
  await postJson("/api/acknowledge", { actor: "laboratorio" });
});

syncButton.addEventListener("click", async () => {
  await postJson("/api/request-sync");
});

monitorButton.addEventListener("click", async () => {
  if (!currentState) {
    return;
  }
  await postJson("/api/monitoring", { enabled: !currentState.monitoring_enabled });
});

async function bootstrap() {
  const initialState = await fetch("/api/state").then((response) => response.json());
  renderState(initialState);

  const stream = new EventSource("/api/events");
  stream.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "state") {
      renderState(payload.state);
    }
  };
}

bootstrap();

