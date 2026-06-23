export type BackendStatus = {
  installed: boolean;
  running: boolean;
  ready: boolean;
  url: string;
  port: number;
  installing: boolean;
  dataDir: string;
  runtimeDir: string;
};

export type AppUpdateRelease = {
  version: string;
  name: string;
  publishedAt: string | null;
  notes: string;
  url: string;
};

export type AppUpdateStatus = {
  status: 'idle' | 'development' | 'checking' | 'current' | 'available' | 'downloading' | 'downloaded' | 'error';
  currentVersion: string;
  latestVersion: string | null;
  releaseDate: string | null;
  releaseNotes: string;
  history: AppUpdateRelease[];
  progress: number | null;
  message: string;
};

export type DubcutLogEvent = {
  channel: string;
  line: string;
};

export type DubcutMediaFile = {
  path: string;
  name: string;
  size: number;
  url: string;
};

export type DubcutLogoFile = {
  path: string;
  name: string;
  size: number;
  url: string;
};

export type DubcutVoiceSampleFile = {
  path: string;
  name: string;
  size: number;
  url: string;
};

declare global {
  interface Window {
    dubcut?: {
      getStatus: () => Promise<BackendStatus>;
      getUpdateStatus: () => Promise<AppUpdateStatus>;
      checkUpdates: () => Promise<AppUpdateStatus>;
      downloadUpdate: () => Promise<AppUpdateStatus>;
      getUpdateHistory: () => Promise<AppUpdateRelease[]>;
      install: (target?: 'common' | 'shorts' | 'dubmaster' | 'all' | 'music' | 'videogen') => Promise<BackendStatus>;
      uninstall: (target?: 'common' | 'shorts' | 'dubmaster' | 'all' | 'music' | 'videogen') => Promise<BackendStatus>;
      startBackend: () => Promise<BackendStatus>;
      getLogs: () => Promise<string>;
      openRuntime: () => Promise<BackendStatus>;
      chooseVideo: () => Promise<DubcutMediaFile | null>;
      chooseLogo: () => Promise<DubcutLogoFile | null>;
      chooseVoiceSample: () => Promise<DubcutVoiceSampleFile | null>;
      chooseImage: () => Promise<DubcutMediaFile | null>;
      chooseWorkDir: () => Promise<{ path: string } | null>;
      openPath: (path: string) => Promise<boolean>;
      revealPath: (path: string) => Promise<boolean>;
      getPathForFile: (file: File) => string;
      onLog: (callback: (payload: DubcutLogEvent) => void) => () => void;
      onStatus: (callback: (payload: BackendStatus) => void) => () => void;
      onUpdateStatus: (callback: (payload: AppUpdateStatus) => void) => () => void;
    };
  }
}
