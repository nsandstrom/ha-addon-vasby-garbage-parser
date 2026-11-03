// Page logic for the waste schedule add-on.
// Talks to server.py: GET status, GET current, POST upload, POST persist, POST rollback.
// All URLs are relative so the page works under the Home Assistant ingress path.

const $ = (id) => document.getElementById(id);

// Identifies the YAML currently shown in the preview. The server hands it out
// on upload and requires it back on persist, so a stale preview can't be saved.
let previewToken = null;


// --- helpers ---------------------------------------------------------------

/** Show a short message next to a button. kind: "mut" | "ok" | "err". */
function setMessage(element, text, kind = "mut") {
  element.textContent = text;
  element.className = kind;
}

/** POST and always resolve to a result object: { ok, error?, ... }. */
async function post(url, body, headers = {}) {
  let response;
  try {
    response = await fetch(url, { method: "POST", body, headers });
  } catch (err) {
    return { ok: false, error: "Could not reach the server." };
  }
  try {
    return await response.json();
  } catch (err) {
    return { ok: false, error: `Bad response (${response.status}).` };
  }
}

/** Copy text to the clipboard, with a fallback for contexts that block the API. */
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    // Fall through: clipboard API missing or denied (e.g. inside an iframe).
  }

  const scratch = document.createElement("textarea");
  scratch.value = text;
  scratch.style.position = "fixed";
  scratch.style.opacity = "0";
  document.body.appendChild(scratch);
  scratch.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (err) {
    copied = false;
  }
  document.body.removeChild(scratch);
  return copied;
}

/** Make a copy button copy the text of another element and flash its tooltip. */
function wireCopyButton(buttonId, sourceId, tipId) {
  const button = $(buttonId);
  const tip = $(tipId);
  let hideTimer;

  button.addEventListener("click", async () => {
    const copied = await copyText($(sourceId).textContent);
    tip.textContent = copied ? "Copied" : "Copy failed";
    tip.classList.add("show");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => tip.classList.remove("show"), 1500);
  });
}


// --- persisted schedule card -----------------------------------------------

function describePersisted(status) {
  if (!status.has_current) {
    return "Nothing persisted yet.";
  }
  const when = status.persisted || "?";
  const source = status.filename ? ` from ${status.filename}` : "";
  return `${status.lines} lines, persisted ${when}${source}. ` +
    `Served read-only on port ${status.file_port} at /sources.yaml.`;
}

function summarisePersisted(status) {
  if (!status.has_current) {
    return "nothing persisted yet";
  }
  return `${status.lines} lines, ${status.persisted || ""}`;
}

async function refreshPersisted() {
  let status;
  try {
    status = await (await fetch("status")).json();
  } catch (err) {
    $("curtxt").textContent = "Could not load the persisted schedule.";
    return;
  }

  $("curtxt").textContent = describePersisted(status);
  $("cursum").textContent = summarisePersisted(status);
  $("rb").disabled = !status.has_previous;
  $("curbox").hidden = !status.has_current;

  if (status.has_current) {
    $("curyaml").textContent = await (await fetch("current")).text();
  }
}


// --- preview ---------------------------------------------------------------

/** Render a unified diff, colouring added and removed lines. */
function renderDiff(diffText) {
  const container = $("diff");
  container.textContent = "";

  for (const line of diffText.split("\n")) {
    const span = document.createElement("span");
    span.textContent = line + "\n";
    if (line.startsWith("+") && !line.startsWith("+++")) {
      span.className = "add";
    } else if (line.startsWith("-") && !line.startsWith("---")) {
      span.className = "del";
    }
    container.appendChild(span);
  }
}

function showPreview(result) {
  previewToken = result.token;
  $("notes").textContent = result.notes || "";
  $("newyaml").textContent = result.yaml;
  renderDiff(result.diff);
  $("pmsg").textContent = "";
  $("persist").disabled = false;
  $("result").hidden = false;
}


// --- actions ---------------------------------------------------------------

function onFileChosen() {
  $("up").disabled = $("file").files.length === 0;
}

async function onUpload() {
  const file = $("file").files[0];
  if (!file) {
    return;
  }

  $("result").hidden = true;
  setMessage($("upmsg"), "Parsing...");
  $("up").disabled = true;

  const params = new URLSearchParams({ filename: file.name });
  if ($("until").value) {
    params.set("until", $("until").value);
  }
  if ($("omit").checked) {
    params.set("omit_empty", "1");
  }

  const result = await post(`upload?${params}`, file, { "Content-Type": "application/pdf" });
  $("up").disabled = false;

  if (!result.ok) {
    const details = result.notes ? `\n${result.notes}` : "";
    setMessage($("upmsg"), result.error + details, "err");
    return;
  }
  setMessage($("upmsg"), "");
  showPreview(result);
}

async function onPersist() {
  $("persist").disabled = true;
  const result = await post(`persist?token=${encodeURIComponent(previewToken)}`);

  if (!result.ok) {
    setMessage($("pmsg"), result.error, "err");
    $("persist").disabled = false;
    return;
  }
  setMessage($("pmsg"), "Persisted.", "ok");
  previewToken = null;
  await refreshPersisted();
}

async function onRollback() {
  if (!confirm("Swap the persisted schedule with the previous version?")) {
    return;
  }
  const result = await post("rollback");
  if (!result.ok) {
    alert(result.error);
  }
  await refreshPersisted();
}


// --- start -----------------------------------------------------------------

$("file").addEventListener("change", onFileChosen);
$("up").addEventListener("click", onUpload);
$("persist").addEventListener("click", onPersist);
$("rb").addEventListener("click", onRollback);
wireCopyButton("copynew", "newyaml", "tipnew");
wireCopyButton("copycur", "curyaml", "tipcur");

refreshPersisted();
