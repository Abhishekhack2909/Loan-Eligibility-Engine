const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#csv-file");
const button = document.querySelector("#upload-button");
const status = document.querySelector("#status");
const result = document.querySelector("#result");
const query = new URLSearchParams(window.location.search);
const apiBaseUrl = (query.get("api") || window.LOAN_API_BASE_URL || "").replace(/\/$/, "");
const apiToken = query.get("token") || window.LOAN_API_TOKEN || "";

function apiUrl(path) {
  return `${apiBaseUrl}${apiBaseUrl ? path : `/api${path}`}`;
}

function apiHeaders() {
  return apiToken ? { "X-API-Key": apiToken } : {};
}

function setStatus(message, kind = "") {
  status.textContent = message;
  status.className = `status ${kind}`;
}

function browserStorageUrl(url) {
  // The backend signs against the container hostname. Nginx preserves that
  // Host header while proxying /storage to MinIO, keeping browser uploads local.
  return url.replace(/^https?:\/\/minio:9000/, "/storage");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;

  button.disabled = true;
  result.hidden = true;
  setStatus("Requesting a presigned upload URL…");
  try {
    const metadataResponse = await fetch(apiUrl("/uploads"), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...apiHeaders() },
      body: JSON.stringify({ filename: file.name, content_type: file.type || "text/csv" }),
    });
    if (!metadataResponse.ok) throw new Error(`Backend returned HTTP ${metadataResponse.status}`);
    const metadata = await metadataResponse.json();

    setStatus("Uploading directly to local MinIO…");
    const uploadResponse = await fetch(browserStorageUrl(metadata.upload_url), {
      method: "PUT",
      headers: { "Content-Type": file.type || "text/csv" },
      body: file,
    });
    if (!uploadResponse.ok) throw new Error(`Object storage returned HTTP ${uploadResponse.status}`);

    setStatus("Upload accepted. Waiting for ingestion to complete…", "success");
    const finalStatus = await waitForCompletion(metadata.upload_id);
    result.hidden = false;
    result.textContent = JSON.stringify({
      upload_id: metadata.upload_id,
      object_key: metadata.object_key,
      status: finalStatus.status,
      row_count: finalStatus.row_count,
      error_count: finalStatus.error_count,
    }, null, 2);
    setStatus(
      finalStatus.status === "completed"
        ? "Ingestion completed successfully."
        : `Ingestion finished with status: ${finalStatus.status}`,
      finalStatus.status === "completed" ? "success" : "error",
    );
  } catch (error) {
    setStatus(error.message || "Upload failed.", "error");
  } finally {
    button.disabled = false;
  }
});

async function waitForCompletion(uploadId) {
  const deadline = Date.now() + 5 * 60 * 1000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const response = await fetch(apiUrl(`/uploads/${uploadId}`), {
      headers: apiHeaders(),
    });
    if (!response.ok) throw new Error(`Status check returned HTTP ${response.status}`);
    const body = await response.json();
    if (!["created", "processing"].includes(body.status)) return body;
  }
  throw new Error("Timed out waiting for ingestion status.");
}
