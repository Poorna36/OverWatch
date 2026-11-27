# OverWatch frontends

This folder contains two separate apps:

- `web/` — the Lovable web frontend (Vite + React)
- `mobile/` — the Expo app, intended to run in Expo Go

## Web

```sh
cd frontend/web
npm install
npm run dev
```

## Mobile (Expo Go)

```sh
cd frontend/mobile
npm install
npm start
```

Stop any existing Expo server first (`Ctrl+C`). Run `npm start` from `frontend/mobile`; it clears Metro's cache and starts the `OverWatch` app over LAN. Scan the **new QR code** with Expo Go (don't reopen an older entry in Development Sessions). If your phone can't connect over the same Wi-Fi, run `npm run start:tunnel` from `frontend/mobile` instead. In Expo Go, choose **Scan QR code** and reload after the new bundle opens.

The two apps currently use local mock data; they are not connected to a backend.
