<template>
  <div class="brand-mark" aria-label="HeyBoss Trading Assistant">
    <span class="brand-symbol" aria-hidden="true"><span>H</span></span>
    <span class="brand-copy">
      <strong>HeyBoss</strong>
      <small>Trading assistant</small>
    </span>
  </div>
</template>

<style scoped>
.brand-mark {
  display: inline-flex;
  align-items: center;
  gap: 12px;
}

.brand-symbol {
  position: relative;
  display: grid;
  width: 38px;
  height: 38px;
  overflow: hidden;
  color: #fff;
  background: #111216;
  border-radius: 12px;
  place-items: center;
}

.brand-symbol::after {
  position: absolute;
  right: -9px;
  bottom: -7px;
  width: 27px;
  height: 27px;
  content: '';
  background: var(--gradient-brand);
  border-radius: 50%;
  filter: blur(1px);
}

.brand-symbol span {
  position: relative;
  z-index: 1;
  font-size: 0.96rem;
  font-weight: 760;
}

.brand-copy {
  display: grid;
  gap: 2px;
}

.brand-copy strong {
  font-size: 0.94rem;
  letter-spacing: -0.02em;
}

.brand-copy small {
  color: var(--color-text-faint);
  font-size: 0.62rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
</style>
