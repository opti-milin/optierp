<script setup lang="ts">
// Docstatus / document-status pill matching ERPNext color semantics.
const props = defineProps<{ status: string | number | boolean }>();

const TONES: Record<string, string> = {
  // generic document states
  Draft: "bg-gray-100 text-gray-700",
  Submitted: "bg-green-100 text-green-800",
  Cancelled: "bg-red-100 text-red-700",
  Active: "bg-green-100 text-green-800",
  Enabled: "bg-green-100 text-green-800",
  Disabled: "bg-red-100 text-red-700",
  // invoice lifecycle
  Paid: "bg-green-100 text-green-800",
  Unpaid: "bg-amber-100 text-amber-800",
  "Partly Paid": "bg-blue-100 text-blue-800",
  Overdue: "bg-red-100 text-red-700",
  Return: "bg-purple-100 text-purple-800",
  // order / fulfilment lifecycle (Modules 03-05)
  "To Receive and Bill": "bg-amber-100 text-amber-800",
  "To Deliver and Bill": "bg-amber-100 text-amber-800",
  "To Receive": "bg-blue-100 text-blue-800",
  "To Deliver": "bg-blue-100 text-blue-800",
  "To Bill": "bg-blue-100 text-blue-800",
  Completed: "bg-green-100 text-green-800",
  Closed: "bg-gray-100 text-gray-700",
  Open: "bg-amber-100 text-amber-800",
  Ordered: "bg-green-100 text-green-800",
  "Partially Ordered": "bg-blue-100 text-blue-800",
  Pending: "bg-amber-100 text-amber-800",
  Expired: "bg-red-100 text-red-700",
  Received: "bg-green-100 text-green-800",
  // manufacturing (Work Order) lifecycle
  "Not Started": "bg-gray-100 text-gray-700",
  "In Process": "bg-blue-100 text-blue-800",
  Stopped: "bg-red-100 text-red-700",
};

function tone(): string {
  const s = props.status;
  if (s === 1 || s === true) return TONES.Submitted;
  if (s === 2 || s === false) return TONES.Cancelled;
  if (typeof s === "string" && TONES[s]) return TONES[s];
  return "bg-gray-100 text-gray-700";
}

function label(): string {
  const s = props.status;
  if (typeof s === "number") return ["Draft", "Submitted", "Cancelled"][s] ?? String(s);
  if (typeof s === "boolean") return s ? "Active" : "Disabled";
  return s;
}
</script>

<template>
  <span class="inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium" :class="tone()">
    {{ label() }}
  </span>
</template>
