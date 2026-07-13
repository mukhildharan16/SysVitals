use reqwest::{Client, Method};
use serde::Serialize;
use std::time::Duration;
use tauri::Manager;
use url::Url;

#[derive(Serialize)]
struct ApiResponse {
    status: u16,
    content_type: Option<String>,
    body: String,
}

/// Fetch the configured SysVitals API from Rust, avoiding webview CORS limits.
#[tauri::command]
async fn api_request(
    base_url: String,
    path: String,
    method: String,
    body: Option<String>,
    authorization: Option<String>,
) -> Result<ApiResponse, String> {
    if !path.starts_with('/') || path.starts_with("//") {
        return Err("API path must be a local path beginning with '/'.".into());
    }

    let mut base = Url::parse(&base_url).map_err(|_| "Invalid SysVitals API URL.")?;
    if !matches!(base.scheme(), "http" | "https") {
        return Err("SysVitals API URL must use HTTP or HTTPS.".into());
    }
    if base.path().is_empty() {
        base.set_path("/");
    }
    let url = base
        .join(path.trim_start_matches('/'))
        .map_err(|_| "Invalid SysVitals API path.")?;
    let method = Method::from_bytes(method.as_bytes()).map_err(|_| "Invalid HTTP method.")?;

    let client = Client::builder()
        .timeout(Duration::from_secs(90))
        .build()
        .map_err(|error| error.to_string())?;
    let mut request = client.request(method, url);
    if let Some(authorization) = authorization {
        request = request.header("authorization", authorization);
    }
    if let Some(body) = body {
        request = request.header("content-type", "application/json").body(body);
    }
    let response = request.send().await.map_err(|error| error.to_string())?;
    let status = response.status().as_u16();
    let content_type = response
        .headers()
        .get("content-type")
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned);
    let body = response.text().await.map_err(|error| error.to_string())?;

    Ok(ApiResponse { status, content_type, body })
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![api_request])
        .setup(|app| {
            let window = app.get_webview_window("main").expect("main window missing");
            window.show().expect("failed to show main window");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running SysVitals desktop");
}
