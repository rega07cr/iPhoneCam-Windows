# 📷 iPhoneCam per Windows

[![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D4?logo=windows)](https://github.com/rega07cr/iPhoneCam-Windows)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/rega07cr/iPhoneCam-Windows)](https://github.com/rega07cr/iPhoneCam-Windows/releases)

Usa il tuo **iPhone come webcam del PC** via cavo **USB** o **Wi-Fi**.
**Nessuna app da installare sull'iPhone**: inquadri il QR, si apre Safari, e sei live.

## ✨ Funzionalità (v2)

- 🎥 **Streaming WebRTC** 480p / 720p / 1080p con anteprima live sul PC
- 📱 **Zero installazione su iOS** — solo Safari + QR code
- 🔌 **USB o Wi-Fi** — tethering USB rilevato in automatico (`172.20.10.x`)
- 🎛️ **Controlli remoti dal PC**: cambio camera, torcia, qualità, test latenza
- 📸 **Foto**: scatto lato PC o remoto in alta qualità dall'iPhone, galleria integrata
- ⏺️ **Registrazione video** WebM con un clic
- 📊 **Statistiche live**: risoluzione, FPS, bitrate, latenza, batteria iPhone
- 🪞 Specchio, adattamento video, schermo intero, **modalità popout per OBS**
- 🌙 Tema chiaro/scuro, impostazioni salvate, riconnessione automatica
- 🔒 HTTPS locale con certificato auto-generato + codice di pairing rigenerabile

## 🚀 Uso rapido

1. Scarica l'ultima [Release](https://github.com/rega07cr/iPhoneCam-Windows/releases)
   oppure clona e avvia i sorgenti:
   ```bat
   Avvia-iPhoneCam.bat
   ```
2. Si apre la dashboard su `https://127.0.0.1:4443/` (accetta l'avviso certificato).
3. Inquadra il **QR** con la Fotocamera dell'iPhone → **Apri in Safari**.
4. Sull'iPhone: accetta il certificato **una volta sola** → **Consenti** fotocamera → **LIVE** 🎉

> Tieni Safari in primo piano con lo schermo acceso durante lo streaming.

## 🔌 USB vs Wi-Fi

| | Wi-Fi | USB |
|---|---|---|
| Requisiti | Stesso Wi-Fi (5 GHz consigliato) | Cavo + *Apple Devices*/iTunes, Hotspot personale → Solo USB |
| Latenza tipica | 120–180 ms | 60–90 ms |
| IP | `192.168.x.x` | `172.20.10.x` (rilevato da solo) |

## 🎙️ Usarla in Zoom / Teams / Meet / Discord / OBS

1. Installa una volta **OBS Virtual Camera** (da OBS Studio 28+ è integrata).
2. In OBS: sorgente **Browser** → URL `https://127.0.0.1:4443/?popout=1` → **Avvia Virtual Camera**.
3. In Zoom/Teams seleziona **OBS Virtual Camera** come webcam.

> Il driver virtuale diretto (senza OBS) è in roadmap.

## 🛠️ Sviluppo

```bat
py -m pip install -r requirements.txt
py app.py [--port 4443] [--no-browser]
```

Crea l'eseguibile singolo:

```bat
build-exe.bat
:: produce dist\iPhoneCam.exe
```

Le release GitHub compilano l'EXE in automatico a ogni tag `v*`.

## ❓ Problemi frequenti

- **Il QR non si apre / pagina non raggiungibile** → stesso Wi-Fi? Firewall: consenti Python sulla rete *privata* (o esegui `Apri-Firewall.bat` come amministratore).
- **Fotocamera nera su iPhone** → ricarica la pagina e premi Consenti; `http` non basta, serve `https`.
- **Scatti/lag** → passa a 720p o 480p dai controlli remoti.
- **Si disconnette** → iOS mette in pausa Safari in background: tienilo in primo piano.

## 📄 Licenza

MIT — vedi [LICENSE](LICENSE).
