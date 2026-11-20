/// <reference types="vite/client" />

interface Window {
  agentflow?: {
    getBackendUrl: () => Promise<string>;
    backendStatus: () => Promise<unknown>;
    retryBackend: () => Promise<boolean>;
    selectFile: () => Promise<string | null>;
    openPath: (p: string) => Promise<{ ok: boolean; error?: string }>;
  };
}
