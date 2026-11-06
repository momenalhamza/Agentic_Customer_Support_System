// Phase D (voice input): thin wrapper around the browser's native
// SpeechRecognition API (Web Speech API) — client-side only, no backend
// change. Firefox does not implement this API at all; Chrome/Edge/Opera
// expose it as `webkitSpeechRecognition`, some newer browsers as the
// unprefixed `SpeechRecognition`. TypeScript's DOM lib doesn't ship types
// for this (it's still non-standard/vendor-prefixed), so the shapes below
// are minimal local types for exactly what this file touches — not a full
// spec type.

interface SpeechRecognitionResultLike {
  0: { transcript: string };
}

interface SpeechRecognitionEventLike {
  results: ArrayLike<SpeechRecognitionResultLike>;
}

interface SpeechRecognitionErrorEventLike {
  error: string;
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

function getSpeechRecognitionConstructor(): SpeechRecognitionConstructor | undefined {
  if (typeof window === "undefined") return undefined;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return w.SpeechRecognition || w.webkitSpeechRecognition;
}

export function isSpeechRecognitionSupported(): boolean {
  return Boolean(getSpeechRecognitionConstructor());
}

const ERROR_MESSAGES: Record<string, string> = {
  // "not-allowed" fires both when the user actually clicks Deny AND when
  // the browser silently refuses to even show the permission prompt (e.g.
  // an embedded webview like VS Code's Simple Browser, which typically has
  // no getUserMedia support at all) — the API gives no way to tell those
  // apart, so the message below covers both self-service fixes at once.
  "not-allowed": "Microphone access was denied. If you're in an embedded preview (e.g. VS Code's Simple Browser), open this page in a real browser tab instead. In a real browser, click the padlock icon in the address bar → Site settings → set Microphone to \"Ask\", then reload.",
  "no-speech": "No speech detected — try again.",
  "audio-capture": "No microphone was found.",
  network: "A network error interrupted voice recognition.",
};

export interface VoiceInputHandlers {
  onResult: (transcript: string) => void;
  onError: (message: string) => void;
  onEnd: () => void;
}

export interface VoiceInputController {
  start: () => void;
  stop: () => void;
}

// Returns null if the browser doesn't support speech recognition at all —
// callers use this to decide whether to render the mic button in the
// first place, rather than showing a control that would immediately fail.
export function createVoiceInput(handlers: VoiceInputHandlers): VoiceInputController | null {
  const Ctor = getSpeechRecognitionConstructor();
  if (!Ctor) return null;

  const recognition = new Ctor();
  recognition.lang = "en-US";
  // Single utterance, not continuous — matches a press-to-talk chat input
  // rather than an always-listening dictation mode.
  recognition.continuous = false;
  recognition.interimResults = true;

  recognition.onresult = (event) => {
    const transcript = Array.from(event.results)
      .map((result) => result[0].transcript)
      .join(" ");
    handlers.onResult(transcript);
  };

  recognition.onerror = (event) => {
    handlers.onError(ERROR_MESSAGES[event.error] || "Voice input failed — please try again.");
  };

  recognition.onend = () => {
    handlers.onEnd();
  };

  return {
    start: () => recognition.start(),
    stop: () => recognition.stop(),
  };
}
