import { redirect } from "next/navigation";

/**
 * Leaders & Laggards has been integrated directly into the Executive Overview page (/),
 * consolidating macro trends, category rotation, relative alpha, and the 4-quadrant matrix
 * into a single unified analytics dashboard.
 */
export default function LeadersPage() {
  redirect("/?tab=leaders");
}
