<script setup lang="ts">
import { computed, ref } from "vue";

interface ItemOption {
  value: string;
  label: string;
}

const props = withDefaults(defineProps<{
  modelValue: string | null | undefined;
  options: ItemOption[];
  placeholder?: string;
}>(), {
  placeholder: "Select item…",
});

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();

const open = ref(false);
const query = ref("");

const selectedLabel = computed(() =>
  props.options.find((opt) => opt.value === props.modelValue)?.label ?? "",
);

const visibleValue = computed(() =>
  open.value ? query.value : selectedLabel.value,
);

const matches = computed(() => {
  const q = query.value.trim().toLowerCase();
  const base = q
    ? props.options.filter((opt) => opt.label.toLowerCase().includes(q))
    : props.options;
  return base.slice(0, 12);
});

function onFocus(): void {
  query.value = selectedLabel.value;
  open.value = true;
}

function onInput(event: Event): void {
  query.value = (event.target as HTMLInputElement).value;
  open.value = true;
}

function selectItem(opt: ItemOption): void {
  emit("update:modelValue", opt.value);
  query.value = "";
  open.value = false;
}

function onBlur(): void {
  window.setTimeout(() => {
    open.value = false;
    query.value = "";
  }, 150);
}
</script>

<template>
  <div class="relative">
    <input
      class="form-input"
      :value="visibleValue"
      :placeholder="placeholder"
      @focus="onFocus"
      @input="onInput"
      @blur="onBlur"
    />
    <ul
      v-if="open && matches.length"
      class="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-md border border-gray-200 bg-white text-left shadow-lg"
    >
      <li
        v-for="opt in matches"
        :key="opt.value"
        class="cursor-pointer border-b border-gray-50 px-3 py-2 text-sm last:border-0 hover:bg-primary/5"
        @mousedown.prevent="selectItem(opt)"
      >
        {{ opt.label }}
      </li>
    </ul>
  </div>
</template>
