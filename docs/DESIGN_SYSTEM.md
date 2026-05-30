# Design System

## Overview

`perf-lab-web` uses a cyber-athletic dark theme built on Tailwind CSS v4, zinc surfaces, Geist typography, motion, and three custom neon accents.

The visual language should feel like a high-contrast digital twin console: data-dense, technical, and athletic without becoming noisy.

## Color Palette

### Base Surfaces

| Usage | Class | Hex / Meaning |
|---|---|---|
| Page background | `bg-zinc-950` | `#09090b` |
| Panel background | `bg-zinc-900/70` | translucent panel |
| Input / dark fill | `bg-zinc-800` or `bg-black/50` | dark controls |
| Dividers | `border-white/10` | subtle border |
| Primary text | `text-zinc-100`, `text-white` | main text |
| Secondary text | `text-zinc-400` | captions |
| Muted text | `text-zinc-500`, `text-zinc-600` | labels / metadata |

### Neon Accents

Defined in `tailwind.config.js`:

| Token | Hex | Semantic use |
|---|---|---|
| `neon-cyan` | `#00f5ff` | primary actions, readiness, active state, focus borders |
| `neon-magenta` | `#ff00aa` | habit/dose accents, secondary highlights |
| `neon-violet` | `#8b00ff` | skill/goal/prescription accents |

Common classes:

```text
text-neon-cyan
text-neon-magenta
text-neon-violet
bg-neon-cyan
border-neon-cyan
shadow-neon-cyan
```

### Semantic Status Colors

| Usage | Classes |
|---|---|
| Warnings / weak-point chips | amber classes such as `bg-amber-900/30`, `text-amber-300` |
| Errors | rose classes such as `border-rose-400/60`, `bg-rose-950/40`, `text-rose-200` |
| Benchmark flag | violet badge pattern |
| Deload flag | amber badge pattern |
| Dim metadata | `text-zinc-500`, `border-zinc-700` |

## Typography

Font:

```text
@fontsource-variable/geist
```

Typical styles:

| Use | Classes |
|---|---|
| Main headline | `text-5xl font-semibold tracking-tighter` |
| Panel heading | `text-lg font-semibold tracking-tight text-white` |
| Section label | `text-xs font-bold uppercase tracking-widest text-zinc-500` |
| Metric labels | `text-xs font-mono tracking-widest` |
| Body | `text-sm text-zinc-100` |
| Caption | `text-xs text-zinc-400` |
| Numeric / technical values | `font-mono` |

Gradient headline pattern:

```tsx
<span className="bg-gradient-to-r from-neon-cyan via-neon-magenta to-neon-violet bg-clip-text text-transparent">
  Digital Twin
</span>
```

## Card Pattern

Use translucent dark cards:

```tsx
<Card className="border-white/10 bg-zinc-900/70 backdrop-blur-2xl overflow-hidden">
  <CardContent className="p-6">
    {/* content */}
  </CardContent>
</Card>
```

Guidelines:

- prefer `bg-zinc-900/70` over opaque `bg-zinc-900`
- keep `border-white/10` subtle
- use `backdrop-blur-2xl` for glass depth
- use `overflow-hidden` when child glow/progress elements could bleed

## Buttons

Primary action:

```tsx
<Button className="bg-neon-cyan text-black font-semibold">
  Create Block
</Button>
```

Gradient action:

```tsx
<button className="bg-gradient-to-r from-neon-cyan to-neon-violet text-black font-semibold">
  Log in
</button>
```

Secondary action:

```tsx
<Button variant="outline">
  Refresh
</Button>
```

## Badges

Primary duration/action badge:

```tsx
<Badge className="bg-neon-cyan text-black font-medium">
  60 min
</Badge>
```

Dim metadata badge:

```tsx
<span className="text-xs text-zinc-500 border border-zinc-700 rounded px-1.5 py-0.5">
  v0.3
</span>
```

Planning flags:

```tsx
<Badge className="bg-amber-700/50 text-amber-100">deload</Badge>
<Badge className="bg-violet-700/50 text-violet-100">benchmark</Badge>
```

Weak-point chips:

```tsx
<span className="text-xs bg-amber-900/30 text-amber-300 rounded px-1.5 py-0.5">
  grip
</span>
```

## Live Status Badge

Use for the header identity marker:

```tsx
<div className="inline-flex items-center gap-2 rounded-3xl border border-neon-cyan/30 bg-black/40 px-4 py-1.5 text-xs font-bold uppercase tracking-[1.5px] text-neon-cyan shadow-[0_0_20px_-4px] shadow-neon-cyan">
  <div className="h-2 w-2 animate-pulse rounded-full bg-neon-cyan" />
  PERF LAB • LIVE
</div>
```

## Neon Grid Background

Subtle background texture:

```tsx
<div className="fixed inset-0 bg-[radial-gradient(#00f5ff_0.8px,transparent_0.8px)] bg-[length:40px_40px] opacity-[0.03] pointer-events-none" />
```

Do not increase opacity casually. It becomes noisy fast.

## Inputs and Selects

Typical dark input:

```tsx
<Input className="bg-zinc-800 border-white/10 text-zinc-100 placeholder-zinc-600" />
```

Planning panel uses:

```tsx
<Input className="bg-black/50 border-white/20 text-white" />
```

Select trigger:

```tsx
<SelectTrigger className="bg-zinc-800 border-white/10 text-zinc-100">
  <SelectValue />
</SelectTrigger>
```

## Tables

Planning panel uses shadcn table components.

Guidelines:

- table headers: `text-zinc-300`
- row text: `text-zinc-100` / `text-zinc-200`
- use compact status badges for session status and flags

## Animation Conventions

Use Framer Motion for:

- panel entrance
- tab/surface transitions
- content reveal
- progress-bar fills
- button press feedback

Common page transition:

```tsx
<motion.div
  initial={{ opacity: 0, y: 20 }}
  animate={{ opacity: 1, y: 0 }}
  transition={{ duration: 0.4 }}
>
```

Progress fill:

```tsx
<motion.div
  initial={{ width: 0 }}
  animate={{ width: `${pct}%` }}
  transition={{ duration: 1, ease: "easeOut" }}
/>
```

Avoid expensive layout animations on large lists unless needed.

## shadcn/ui Components Used

Current uploaded source uses:

- `Button`
- `Card`, `CardContent`, `CardHeader`, `CardTitle`
- `Badge`
- `Input`
- `Label`
- `Select`, `SelectContent`, `SelectItem`, `SelectTrigger`, `SelectValue`
- `Table`, `TableBody`, `TableCell`, `TableHead`, `TableHeader`, `TableRow`

Do not move `src/components/ui/` if using shadcn conventions.

## Layout Conventions

General:

- cards use `space-y-*` for vertical rhythm
- use responsive grids for forms: `grid grid-cols-1 md:grid-cols-3 gap-4`
- keep the app optimized for desktop / landscape tablet first
- mobile support should be functional but is not yet the primary layout target

## Auth Strip Pattern

Unauthenticated:

- email input
- password input
- register button
- login submit button
- local error block

Authenticated:

- gradient avatar initial
- signed-in email
- logout button

Keep auth UI compact enough to live in the header.

## Planning Panel Pattern

Planning UI uses three card sections:

1. create planning block
2. blocks list
3. session calendar MVP list

Deload and benchmark flags should remain visually distinct.

## Accessibility Notes

Current UI is visually strong but should continue improving:

- labels should use `htmlFor` where possible
- buttons need clear disabled states
- color should not be the only status indicator
- tables should keep semantic header/body structure
- focus borders should remain visible against dark backgrounds

## Do Not Break

- neon token names
- `@` path alias
- card translucency
- Geist font import
- dark zinc base
- amber weak-point/constraint chips
- planning deload/benchmark badge distinction
