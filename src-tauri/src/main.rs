// VSM-OCR — wrapper desktop Tauri.
// Rôle : lancer le backend Python local (127.0.0.1:8741) en sous-process au
// démarrage, ouvrir la fenêtre sur le frontend bundlé, et tuer proprement le
// backend à la fermeture. Pas de télémétrie, pas d'auto-update (garde-fous).
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

use std::process::{Child, Command};
use std::sync::Mutex;

struct Backend(Mutex<Option<Child>>);

fn spawn_backend() -> Option<Child> {
    // En production, l'interpréteur Python embarqué (ou système) lance le
    // module backend. VSM_DATA_DIR pointe vers le dossier de données chiffrées.
    let python = if cfg!(target_os = "windows") { "python" } else { "python3" };
    Command::new(python)
        .args(["-m", "src.ui_backend.main"])
        .spawn()
        .ok()
}

fn main() {
    let child = spawn_backend();
    tauri::Builder::default()
        .manage(Backend(Mutex::new(child)))
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                let state: tauri::State<Backend> = window.state();
                if let Some(mut c) = state.0.lock().unwrap().take() {
                    let _ = c.kill(); // arrêt du backend = effacement des clés de session
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("erreur au lancement de VSM-OCR");
}
