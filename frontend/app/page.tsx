"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import ChatWindow from "@/components/ChatWindow";
import { getOrCreateSessionId } from "@/lib/session";
import styles from "./page.module.css";

export default function Home() {
  const [sessionId, setSessionId] = useState<string | null>(null);

  useEffect(() => {
    // localStorage doesn't exist during server rendering, so session_id can
    // only be read/created after mount — the resulting one-time re-render
    // (null -> real id) is intentional, not the effect-driven derived-state
    // anti-pattern this lint rule normally targets.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSessionId(getOrCreateSessionId());
  }, []);

  return (
    <div className={styles.page}>
      {sessionId ? <ChatWindow sessionId={sessionId} /> : <p className={styles.loadingShell}>Loading…</p>}
      <Link href="/admin" className={styles.adminLink}>
        Admin
      </Link>
    </div>
  );
}
