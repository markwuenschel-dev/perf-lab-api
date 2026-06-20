# Design System

perf-lab-web uses a **cyber-athletic dark theme** built on Tailwind CSS v4 with
three custom neon accent colors and a zinc-950 base. The visual language
reflects the engine metaphor: data-dense, high-contrast, monospaced accents.

---

## Color Palette

### Base

All surfaces use the Tailwind `zinc` scale:

| Usage | Class | Hex |
|---|---|---|
| Page background | `bg-zinc-950` | `#09090b` |
| Panel background | `bg-zinc-900/70` | `#18181b` at 70% opacity |
| Input / dark fill | `bg-zinc-800` | `#27272a` |
| Dividers | `border-white/10` | white at 10% opacity |
| Primary text | `text-zinc-100` | `#f4f4f5` |
| Secondary text | `text-zinc-400` | `#a1a1aa` |
| Muted / dim text | `text-zinc-500` | `#71717a` |
| Very dim | `text-zinc-600` | `#52525b` |

### Neon Accents

Defined in `src/index.css` in the `@theme inline` block as `--color-neon-*`
(Tailwind v4 is CSS-first — there is no `tailwind.config.js`):

| Token | Hex | Semantic use |
|---|---|---|
| `neon-cyan` (`#00f5ff`) | Electric cyan | Primary actions, readiness, metric labels, the "live" badge, borders on focus |
| `neon-magenta` (`#ff00aa`) | Hot pink | Habit strength, dose labels, secondary accent |
| `neon-violet` (`#8b00ff`) | Deep violet | Skill state, goal alignment, tertiary accent |

**Usage in classes:**
```
text-neon-cyan        bg-neon-cyan        border-neon-cyan
text-neon-magenta     bg-neon-magenta     border-neon-magenta
text-neon-violet      bg-neon-violet      border-neon-violet
shadow-neon-cyan      (glow effect on the live badge)
```

### Status / Semantic Colors

| Usage | Class | Color |
|---|---|---|
| Warnings | `text-amber-400`, `bg-amber-900/30`, `text-amber-300` | Amber |
| Model version badge | `text-zinc-500 border-zinc-700` | Dim zinc |
| Engine version label | `text-zinc-600` | Very dim zinc |

---

## Typography

**Font:** Geist Variable (`@fontsource-variable/geist`) — loaded in `main.tsx`.
Clean, modern, designed for code-adjacent interfaces.

| Use case | Classes |
|---|---|
| Page headline | `text-5xl font-semibold tracking-tighter` |
| Panel heading | `text-lg font-semibold tracking-tight text-white` |
| Section label | `text-xs font-bold uppercase tracking-widest text-zinc-500` |
| Monospaced metric label | `text-xs font-mono tracking-widest text-neon-{color}` |
| Body text | `text-sm text-zinc-100` |
| Secondary / caption | `text-xs text-zinc-400` |
| Code / values | `font-mono` |

**Gradient headline pattern** (used in App.tsx and OnboardingForm.tsx):
```tsx
<span className="bg-gradient-to-r from-neon-cyan via-neon-magenta to-neon-violet bg-clip-text text-transparent">
  Digital Twin
</span>
```

---

## Card Pattern

All panels use the same card foundation:

```tsx
<Card className="border-white/10 bg-zinc-900/70 backdrop-blur-2xl overflow-hidden">
  <CardContent className="p-6">
    {/* content */}
  </CardContent>
</Card>
```

Key attributes:
- `border-white/10` — hairline white border (very subtle)
- `bg-zinc-900/70` — translucent dark panel
- `backdrop-blur-2xl` — frosted glass blur (requires a dark background behind it)
- `overflow-hidden` — prevents child elements from bleeding past rounded corners

**Do not use `bg-zinc-900` (opaque)** for cards — the translucency is part of
the layered depth effect against the `bg-zinc-950` page background.

---

## Badge Pattern

Two badge styles are used:

**Neon primary** (duration, key actions):
```tsx
<Badge className="bg-neon-cyan text-black font-medium">
  60 min
</Badge>
```

**Dim secondary** (engine version, metadata):
```tsx
<span className="text-xs text-zinc-500 border border-zinc-700 rounded px-1.5 py-0.5">
  v0.3
</span>
```

---

## Live Status Badge

The pulsing "PERF LAB • LIVE" badge in the header:

```tsx
<div className="inline-flex items-center gap-2 rounded-3xl border border-neon-cyan/30 bg-black/40 px-4 py-1.5 text-xs font-bold uppercase tracking-[1.5px] text-neon-cyan shadow-[0_0_20px_-4px] shadow-neon-cyan">
  <div className="h-2 w-2 animate-pulse rounded-full bg-neon-cyan" />
  PERF LAB • LIVE
</div>
```

The `shadow-[0_0_20px_-4px] shadow-neon-cyan` creates the neon glow effect.
The `animate-pulse` dot gives the live indicator animation.

---

## Neon Grid Background

Subtle dot grid on the page background (in `App.tsx`):

```tsx
<div className="fixed inset-0 bg-[radial-gradient(#00f5ff_0.8px,transparent_0.8px)] bg-[length:40px_40px] opacity-[0.03] pointer-events-none" />
```

At 3% opacity, it provides texture without distracting from content. Do not
raise the opacity — it becomes visually noisy quickly.

---

## Animation Conventions

The project uses **Framer Motion** for all transitions.

**Page section entrance** (used in `App.tsx` for tab switches):
```tsx
<motion.div
  key={mainTab}
  initial={{ opacity: 0, y: 20 }}
  animate={{ opacity: 1, y: 0 }}
  transition={{ duration: 0.4 }}
>
```

**Content reveal** (used in `DigitalTwinPanel`, `NextSessionCard`):
```tsx
<motion.div
  initial={{ opacity: 0, y: 10 }}
  animate={{ opacity: 1, y: 0 }}
>
```

**Progress bar fill** (used in `FatigueBar`, `SkillPanel`):
```tsx
<motion.div
  initial={{ width: 0 }}
  animate={{ width: `${pct}%` }}
  transition={{ duration: 1, ease: "easeOut" }}
/>
```

**Button press feedback**:
```tsx
<motion.button whileTap={{ scale: 0.95 }}>
```

**Avoid** `layout` animations on list items — they are expensive and the
prescription list updates infrequently enough not to need them.

---

## shadcn/ui Components Used

| Component | Import path | Where used |
|---|---|---|
| `Button` | `@/components/ui/button` | Log, simulate, onboard actions |
| `Card`, `CardContent`, `CardHeader`, `CardTitle` | `@/components/ui/card` | All panels |
| `Badge` | `@/components/ui/badge` | Duration badge |
| `Input` | `@/components/ui/input` | Workout form fields, onboard form |
| `Label` | `@/components/ui/label` | Form field labels |
| `Select`, `SelectContent`, `SelectItem`, `SelectTrigger`, `SelectValue` | `@/components/ui/select` | Modality, goal, movement pattern |
| `Tabs`, `TabsList`, `TabsTrigger` | `@/components/ui/tabs` | Main tab switcher in App |

All shadcn components live in `src/components/ui/` and use the `@` path alias.
Do not move them — shadcn's CLI expects them there.

---

## Weak-Point Tag Chips

Tags from `PrescriptionExplanation.constraints_applied` that start with
`weak_point:` render as amber chips:

```tsx
<span className="text-xs bg-amber-900/30 text-amber-300 rounded px-1.5 py-0.5">
  {tag}
</span>
```

Use this same pattern for any future tag-style UI (equipment constraints, sport
domains, etc.) — maintain consistent amber for constraint signals.

---

## Responsive Behavior

The app is optimized for desktop / landscape tablet. Key breakpoints:

- Header: stacks vertically on mobile, row on `lg:`
- Summary strip: always 3 columns (`grid-cols-3`) — does not collapse on mobile
- Twin panel: single column stacked layout on small screens

No explicit mobile-first design has been done beyond what Tailwind's defaults
provide. This is a known limitation — the UI is designed for coach/athlete use
on a desktop or large tablet screen.
