/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_PIPER_WS_URL?: string;
  readonly VITE_PIPER_EXPERIMENTAL_MIC_UPLOAD?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
