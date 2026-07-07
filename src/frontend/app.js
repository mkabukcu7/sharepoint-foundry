// Minimal chat client for the Workplace Assistant stub.
const chat = document.getElementById("chat");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const send = document.getElementById("send");

let threadId = null;

function addMessage(role, text, { degraded = false, citations = [] } = {}) {
  const msg = document.createElement("div");
  msg.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble" + (degraded ? " degraded" : "");
  bubble.textContent = text;
  if (citations.length) {
    const cites = document.createElement("div");
    cites.className = "citations";
    cites.innerHTML =
      "Sources: " +
      citations
        .map((c) =>
          c.source_url
            ? `<a href="${c.source_url}" target="_blank" rel="noopener">${c.title || c.source_url}</a>`
            : c.title || "source"
        )
        .join(" &middot; ");
    bubble.appendChild(cites);
  }
  msg.appendChild(bubble);
  chat.appendChild(msg);
  chat.scrollTop = chat.scrollHeight;
  return bubble;
}

async function sendMessage(message) {
  const typing = addMessage("assistant", "Thinking…");
  typing.classList.add("typing");
  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, thread_id: threadId }),
    });
    const data = await res.json();
    threadId = data.thread_id || threadId;
    typing.parentElement.remove();
    addMessage("assistant", data.answer || "(no answer)", {
      degraded: data.degraded,
      citations: data.citations || [],
    });
  } catch (err) {
    typing.parentElement.remove();
    addMessage("assistant", "Sorry, something went wrong reaching the service.", {
      degraded: true,
    });
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  addMessage("user", message);
  input.value = "";
  send.disabled = true;
  sendMessage(message).finally(() => {
    send.disabled = false;
    input.focus();
  });
});
