import AdminDashboard from "@/components/AdminDashboard";

// Phase C: unauthenticated by design for now — see ANALYSIS.md's Phase C
// entry for the tradeoff. It only reads /metrics, /escalations, and
// /feedback, all of which are themselves unauthenticated backend routes
// today, so this page adds no new exposure; it's flagged as a real gap if
// this project is ever deployed somewhere reachable by the public.
export default function AdminPage() {
  return <AdminDashboard />;
}
