<script setup lang="ts">
import { X } from '@lucide/vue'
import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    description?: string
    width?: string
    fallbackFocus?: HTMLElement | null
  }>(),
  {
    description: '',
    width: '560px',
    fallbackFocus: null,
  },
)

const emit = defineEmits<{
  close: []
}>()

const panel = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
const titleId = useId()
const descriptionId = useId()
let previousFocus: HTMLElement | null = null
let active = false
let disposed = false

function canFocus(element: HTMLElement | null | undefined): element is HTMLElement {
  return (
    !!element?.isConnected &&
    element !== document.body &&
    element.matches('a[href], button, input, select, textarea, [tabindex]') &&
    !element.matches(':disabled, [aria-disabled="true"]') &&
    !element.closest('[hidden], [inert], [aria-hidden="true"]')
  )
}

function focusableElements(): HTMLElement[] {
  if (!panel.value) {
    return []
  }
  return Array.from(
    panel.value.querySelectorAll<HTMLElement>(
      'a[href], button, input, select, textarea, [tabindex]',
    ),
  ).filter((element) => canFocus(element) && element.tabIndex >= 0)
}

function handleKeydown(event: KeyboardEvent): void {
  if (!props.open) {
    return
  }
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key !== 'Tab') {
    return
  }
  const elements = focusableElements()
  if (elements.length === 0) {
    event.preventDefault()
    panel.value?.focus({ preventScroll: true })
    return
  }
  const first = elements[0]
  const last = elements.at(-1)
  if (!panel.value?.contains(document.activeElement)) {
    event.preventDefault()
    ;(event.shiftKey ? last : first)?.focus({ preventScroll: true })
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}

function release(): void {
  if (!active) return
  active = false
  document.removeEventListener('keydown', handleKeydown)
  document.body.classList.remove('drawer-open')
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      active = true
      previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
      document.addEventListener('keydown', handleKeydown)
      document.body.classList.add('drawer-open')
      await nextTick()
      if (props.open && !disposed) closeButton.value?.focus({ preventScroll: true })
      return
    }
    if (!active) return
    const target = previousFocus
    release()
    previousFocus = null
    await nextTick()
    if (props.open || disposed) return
    if (canFocus(target)) target.focus({ preventScroll: true })
    else if (canFocus(props.fallbackFocus)) props.fallbackFocus.focus({ preventScroll: true })
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  disposed = true
  if (!active) return
  const target = previousFocus
  release()
  previousFocus = null
  void nextTick(() => {
    if (canFocus(target)) target.focus({ preventScroll: true })
  })
})
</script>

<template>
  <Teleport to="body">
    <Transition name="drawer-fade">
      <div v-if="open" class="drawer-backdrop" @mousedown.self="emit('close')">
        <section
          ref="panel"
          class="drawer-panel"
          :style="{ '--drawer-width': width }"
          role="dialog"
          aria-modal="true"
          :aria-labelledby="titleId"
          :aria-describedby="description ? descriptionId : undefined"
          tabindex="-1"
        >
          <header>
            <div>
              <p class="eyebrow">Details</p>
              <h2 :id="titleId">{{ title }}</h2>
              <p v-if="description" :id="descriptionId">{{ description }}</p>
            </div>
            <button ref="closeButton" type="button" aria-label="关闭详情" @click="emit('close')">
              <X :size="19" aria-hidden="true" />
            </button>
          </header>
          <div class="drawer-content">
            <slot />
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer-backdrop {
  position: fixed;
  z-index: 100;
  display: flex;
  align-items: stretch;
  justify-content: flex-end;
  padding: 10px;
  background: rgb(15 17 23 / 38%);
  backdrop-filter: blur(7px);
  inset: 0;
}

.drawer-panel {
  display: grid;
  width: min(var(--drawer-width), calc(100vw - 20px));
  min-width: 0;
  overflow: hidden;
  background: var(--color-surface);
  border: 1px solid rgb(255 255 255 / 54%);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-float);
  grid-template-rows: auto minmax(0, 1fr);
}

.drawer-panel > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding: 24px 24px 19px;
  border-bottom: 1px solid var(--color-line);
}

.drawer-panel h2 {
  margin: 0;
  font-size: 1.26rem;
  letter-spacing: -0.035em;
}

.drawer-panel > header > div {
  min-width: 0;
  overflow-wrap: anywhere;
}

.drawer-panel header p:last-child {
  margin: 7px 0 0;
  color: var(--color-text-soft);
  font-size: 0.76rem;
  line-height: 1.5;
}

.drawer-panel header button {
  display: grid;
  flex: 0 0 auto;
  width: 36px;
  height: 36px;
  color: var(--color-text-soft);
  cursor: pointer;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: 50%;
  place-items: center;
}

.drawer-panel header button:hover {
  color: var(--color-text);
}

.drawer-content {
  min-height: 0;
  padding: 22px 24px 30px;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.drawer-fade-enter-active,
.drawer-fade-leave-active {
  transition: opacity 180ms ease;
}

.drawer-fade-enter-active .drawer-panel,
.drawer-fade-leave-active .drawer-panel {
  transition: transform 220ms ease;
}

.drawer-fade-enter-from,
.drawer-fade-leave-to {
  opacity: 0;
}

.drawer-fade-enter-from .drawer-panel,
.drawer-fade-leave-to .drawer-panel {
  transform: translateX(24px);
}

@media (max-width: 620px) {
  .drawer-backdrop {
    padding: 0;
  }

  .drawer-panel {
    width: 100%;
    border: 0;
    border-radius: 0;
  }

  .drawer-panel > header,
  .drawer-content {
    padding-right: 19px;
    padding-left: 19px;
  }
}
</style>
