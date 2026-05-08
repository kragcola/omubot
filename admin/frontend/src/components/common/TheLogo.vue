<script setup lang="ts">
withDefaults(defineProps<{
  size?: 'md' | 'lg'
}>(), {
  size: 'md',
})
</script>

<template>
  <div class="om-logo" :class="size === 'lg' ? 'om-logo--lg' : 'om-logo--md'">
    <div class="om-logo__core">
      <span>O</span>
    </div>
    <span class="om-logo__pulse" />
  </div>
</template>

<style scoped>
.om-logo {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 18px;
  background:
    radial-gradient(circle at 30% 30%, rgba(255, 255, 255, 0.28), transparent 34%),
    linear-gradient(145deg, rgba(var(--primary-color), 0.95), rgba(var(--primary-color), 0.72));
  color: white;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.18),
    0 16px 28px rgba(var(--primary-color), 0.22);
  user-select: none;
}

.om-logo--md {
  width: 38px;
  height: 38px;
}

.om-logo--lg {
  width: 72px;
  height: 72px;
  border-radius: 22px;
}

.om-logo__core {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 70%;
  height: 70%;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  font-weight: 700;
  letter-spacing: 0.04em;
  backdrop-filter: blur(6px);
}

.om-logo--md .om-logo__core {
  font-size: 18px;
}

.om-logo--lg .om-logo__core {
  font-size: 30px;
}

.om-logo__pulse {
  position: absolute;
  right: 18%;
  bottom: 16%;
  width: 18%;
  height: 18%;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 0 0 6px rgba(255, 255, 255, 0.12);
}
</style>
