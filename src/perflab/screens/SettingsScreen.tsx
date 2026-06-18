// src/perflab/screens/SettingsScreen.tsx
import { cn } from "@/lib/utils";
import { usePerfLab } from "../store";
import type { Settings } from "../store";
import { Card, SectionLabel } from "../ui";

const ACCENTS = ["#c6f135", "#45d6c4", "#86b8ff", "#f5c451", "#ff8a5c"];

const inputCls = "mt-2 w-full rounded-[11px] border border-white/10 bg-panel px-[13px] py-[11px] text-[14px] text-ink";
const segCls = (active: boolean) =>
  cn(
    "flex-1 cursor-pointer rounded-[10px] border p-[11px] text-center text-[13px] font-semibold leading-none",
    active ? "border-ac/40 bg-ac/[0.12] text-ac" : "border-white/10 bg-panel text-mute",
  );

function Seg({ options, value, onChange, className }: { options: string[]; value: string; onChange: (v: string) => void; className?: string }) {
  return (
    <div className={cn("mt-2 flex gap-2", className)}>
      {options.map((o) => (
        <div key={o} onClick={() => onChange(o)} className={segCls(value === o)}>{o}</div>
      ))}
    </div>
  );
}

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <div onClick={onClick} className={cn("relative h-[22px] w-[38px] flex-none cursor-pointer rounded-full transition-colors", on ? "bg-ac" : "bg-white/[0.12]")}>
      <div className={cn("absolute top-[3px] h-[16px] w-[16px] rounded-full transition-all", on ? "left-[19px] bg-[#0a0c10]" : "left-[3px] bg-mute")} />
    </div>
  );
}

export function SettingsScreen() {
  const { state, actions } = usePerfLab();
  const s = state.settings;
  const notif: [keyof Settings, string, string][] = [
    ["notifReadiness", "Readiness alerts", "When readiness crashes below 40."],
    ["notifTissue", "Tissue-load warnings", "When a region exceeds 60."],
    ["notifWeekly", "Weekly summary", "A Monday digest of the week ahead."],
  ];

  return (
    <section className="flex max-w-[780px] flex-col gap-4 px-[30px] pb-9 pt-[26px]">
      <header>
        <h1 className="m-0 text-[25px] font-bold leading-none tracking-[-0.02em] text-ink">Settings</h1>
        <p className="m-0 mt-[9px] text-[13.5px] font-medium leading-[1.5] text-[#7c818c]">Profile, units and preferences — editable any time.</p>
      </header>

      <Card className="p-[22px]">
        <SectionLabel className="mb-4">Profile</SectionLabel>
        <div className="grid grid-cols-2 gap-4">
          <label className="block"><span className="text-[12px] font-medium leading-none text-mute">First name</span><input defaultValue="Alex" className={inputCls} /></label>
          <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Last name</span><input defaultValue="Rivera" className={inputCls} /></label>
          <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Date of birth</span><input type="date" defaultValue="1997-04-12" className={inputCls} style={{ colorScheme: "dark" }} /></label>
          <label className="block"><span className="text-[12px] font-medium leading-none text-mute">Sex</span><Seg options={["Female", "Male"]} value={s.sex} onChange={(v) => actions.setSetting("sex", v)} /></label>
        </div>
        <div className="mt-[14px] flex items-center gap-[9px] rounded-[11px] border border-info/[0.18] bg-info/[0.06] px-3 py-[10px]">
          <span className="text-[13px] text-info">ⓘ</span><span className="text-[11.5px] font-medium leading-[1.45] text-mute">VO₂ doesn't use age &amp; sex in v0.3 — stored for upcoming model versions.</span>
        </div>
      </Card>

      <Card className="p-[22px]">
        <SectionLabel className="mb-4">Training</SectionLabel>
        <div className="flex flex-col gap-4">
          <div>
            <span className="text-[12px] font-medium leading-none text-mute">Primary sport</span>
            <Seg className="flex-wrap" options={["Distance", "Trail / ultra", "Triathlon", "Hybrid"]} value={s.sport} onChange={(v) => actions.setSetting("sport", v)} />
          </div>
          <div>
            <span className="text-[12px] font-medium leading-none text-mute">Units</span>
            <Seg options={["Metric (km)", "Imperial (mi)"]} value={s.units} onChange={(v) => actions.setSetting("units", v)} />
          </div>
          <label className="block max-w-[280px]"><span className="text-[12px] font-medium leading-none text-mute">Current weekly volume</span><input defaultValue="48 km" className={`${inputCls} font-mono`} /></label>
        </div>
      </Card>

      <Card className="p-[22px]">
        <SectionLabel className="mb-[6px]">Accent</SectionLabel>
        <div className="mb-[14px] text-[12px] font-medium leading-[1.5] text-[#7c818c]">Highlight colour for charts and active states.</div>
        <div className="flex items-center gap-4">
          {ACCENTS.map((c) => (
            <div
              key={c}
              onClick={() => actions.setSetting("accent", c)}
              className="h-[30px] w-[30px] cursor-pointer rounded-full"
              style={{ background: c, boxShadow: `0 0 0 2px #111419, 0 0 0 ${s.accent === c ? `4px ${c}` : "2px transparent"}` }}
            />
          ))}
        </div>
      </Card>

      <Card className="p-[22px]">
        <SectionLabel className="mb-4">Notifications</SectionLabel>
        <div className="flex flex-col gap-1">
          {notif.map(([key, title, desc], i) => (
            <div key={key} className={cn("flex items-center justify-between py-[11px]", i < notif.length - 1 && "border-b border-white/[0.05]")}>
              <div>
                <div className="text-[13px] font-semibold leading-none text-[#e6e8ec]">{title}</div>
                <div className="mt-1 text-[11px] font-medium leading-[1.4] text-faint">{desc}</div>
              </div>
              <Toggle on={s[key] as boolean} onClick={() => actions.setSetting(key, !s[key])} />
            </div>
          ))}
        </div>
      </Card>

      <Card className="flex items-center justify-between p-[22px]">
        <div>
          <div className="text-[13px] font-semibold leading-none text-[#e6e8ec]">Model version</div>
          <div className="mt-[5px] font-mono text-[11px] leading-none text-faint">perf-lab-web · S(t) v0.3</div>
        </div>
        <button className="rounded-[9px] border border-hot/25 bg-hot/[0.08] px-4 py-[10px] text-[12.5px] font-semibold leading-none text-hot">Sign out</button>
      </Card>
    </section>
  );
}
