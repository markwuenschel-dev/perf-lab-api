// src/perflab/screens/OnboardingScreen.tsx
import { usePerfLab } from "../store";

const labelCls = "font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.1em] text-[#9aa0ab]";
const inputCls = "mt-[9px] w-full rounded-[11px] border border-white/10 bg-panel px-[14px] py-3 text-[14px] text-ink";
const segOn = "rounded-[11px] border border-ac/40 bg-ac/[0.12] p-3 text-center text-[13px] font-semibold leading-none text-ac";
const segOff = "rounded-[11px] border border-white/10 bg-panel p-3 text-center text-[13px] font-semibold leading-none text-mute";
const btnPrimary = "rounded-[11px] bg-ac px-6 py-[13px] text-[13.5px] font-semibold leading-none text-[#0a0c10]";
const btnBack = "rounded-[11px] border border-white/[0.12] px-[22px] py-[13px] text-[13.5px] font-semibold leading-none text-mute";

const STEPS = [
  ["01", "Field test", "300 m + 1.5 mi → VO₂, profile & pace zones."],
  ["02", "Digital twin", "A state vector S(t) that evolves as you train."],
  ["03", "Adaptive plan", "Sessions prescribed against your readiness."],
];

export function OnboardingScreen() {
  const { state, actions } = usePerfLab();
  const ob = state.obStep;
  const goOverview = () => actions.setScreen("overview");

  return (
    <div className="grid min-h-screen grid-cols-1 md:grid-cols-[minmax(0,440px)_1fr]">
      {/* brand panel */}
      <div className="flex flex-col justify-between border-r border-white/[0.06] px-11 py-12" style={{ background: "radial-gradient(120% 80% at 0% 0%,#11321f,#0b0e13 55%)" }}>
        <div className="flex items-center gap-3">
          <div className="grid h-[34px] w-[34px] place-items-center rounded-[10px] bg-gradient-to-br from-ac to-teal text-[17px] font-extrabold leading-none text-[#0a0c10]">◆</div>
          <div>
            <div className="text-[16px] font-bold leading-none text-ink">PERF LAB</div>
            <div className="mt-[3px] font-mono text-[10px] leading-[1.3] tracking-[0.14em] text-faint">PERFORMANCE OS</div>
          </div>
        </div>
        <div>
          <h2 className="m-0 text-[34px] font-bold leading-[1.1] tracking-[-0.03em] text-ink">Turn two timed runs into a living model of your body.</h2>
          <div className="mt-[30px] flex flex-col gap-[18px]">
            {STEPS.map(([n, t, d]) => (
              <div key={n} className="flex gap-[13px]">
                <span className="font-mono text-[14px] font-bold leading-[1.4] text-ac">{n}</span>
                <div>
                  <div className="text-[14px] font-semibold leading-none text-ink">{t}</div>
                  <div className="mt-[5px] text-[12.5px] font-medium leading-[1.5] text-[#7c818c]">{d}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="font-mono text-[11px] leading-none text-dim">v0.3 · perf-lab-web</div>
      </div>

      {/* form panel */}
      <div className="flex max-w-[620px] flex-col justify-center px-14 py-12">
        <div className="mb-2 flex items-center justify-between">
          <span className="font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.18em] text-faint">Step {ob} of 3</span>
          <button onClick={goOverview} className="border-0 bg-transparent text-[12px] font-medium leading-none text-[#7c818c]">Skip for now →</button>
        </div>
        <div className="mb-[34px] h-1 overflow-hidden rounded-full bg-white/[0.07]">
          <div className="h-full rounded-full transition-all duration-300" style={{ width: ob === 1 ? "33%" : ob === 2 ? "66%" : "100%", background: "linear-gradient(90deg,var(--ac),#7bd6c0)" }} />
        </div>

        {ob === 1 && (
          <div>
            <h1 className="m-0 text-[30px] font-bold leading-[1.1] tracking-[-0.025em] text-ink">Let's set up your profile</h1>
            <p className="m-0 mb-7 mt-3 text-[14px] font-medium leading-[1.5] text-[#7c818c]">Just the basics. You can change any of this later.</p>
            <div className="grid grid-cols-2 gap-4">
              <label className="block"><span className={labelCls}>First name</span><input defaultValue="Alex" className={inputCls} /></label>
              <label className="block"><span className={labelCls}>Last name</span><input defaultValue="Rivera" className={inputCls} /></label>
              <label className="block"><span className={labelCls}>Date of birth</span><input type="date" defaultValue="1997-04-12" className={inputCls} style={{ colorScheme: "dark" }} /></label>
              <label className="block">
                <span className={labelCls}>Sex</span>
                <div className="mt-[9px] grid grid-cols-2 gap-2"><div className={segOn}>Female</div><div className={segOff}>Male</div></div>
              </label>
            </div>
            <div className="mt-[14px] flex items-center gap-[9px] rounded-[11px] border border-info/[0.18] bg-info/[0.06] px-[13px] py-[11px]">
              <span className="text-[14px] text-info">ⓘ</span><span className="text-[12px] font-medium leading-[1.5] text-mute">VO₂ doesn't use age/sex yet — stored for upcoming model versions.</span>
            </div>
            <div className="mt-[30px] flex justify-end"><button onClick={actions.obNext} className={btnPrimary}>Continue →</button></div>
          </div>
        )}

        {ob === 2 && (
          <div>
            <h1 className="m-0 text-[30px] font-bold leading-[1.1] tracking-[-0.025em] text-ink">Training context</h1>
            <p className="m-0 mb-7 mt-3 text-[14px] font-medium leading-[1.5] text-[#7c818c]">So the plan speaks your language.</p>
            <div className="flex flex-col gap-4">
              <label className="block"><span className={labelCls}>Primary sport</span>
                <select defaultValue="Distance running" className={inputCls} style={{ colorScheme: "dark" }}><option>Distance running</option><option>Trail / ultra</option><option>Triathlon</option><option>Hybrid / tactical</option></select>
              </label>
              <label className="block"><span className={labelCls}>Units</span>
                <div className="mt-[9px] grid grid-cols-2 gap-2"><div className={segOn}>Metric (km)</div><div className={segOff}>Imperial (mi)</div></div>
              </label>
              <label className="block"><span className={labelCls}>Current weekly volume</span><input defaultValue="48 km" className={inputCls} /></label>
            </div>
            <div className="mt-[30px] flex justify-between"><button onClick={actions.obBack} className={btnBack}>← Back</button><button onClick={actions.obNext} className={btnPrimary}>Continue →</button></div>
          </div>
        )}

        {ob === 3 && (
          <div>
            <h1 className="m-0 text-[30px] font-bold leading-[1.1] tracking-[-0.025em] text-ink">Seed your twin</h1>
            <p className="m-0 mb-7 mt-3 text-[14px] font-medium leading-[1.5] text-[#7c818c]">Enter a recent field test to initialize S(t) — or skip and run one later.</p>
            <div className="grid grid-cols-2 gap-4">
              <label className="block"><span className={labelCls}>300 m time</span><input defaultValue="0:52" placeholder="M:SS" className={`${inputCls} font-mono`} /></label>
              <label className="block"><span className={labelCls}>1.5 mi time</span><input defaultValue="9:18" placeholder="MM:SS" className={`${inputCls} font-mono`} /></label>
            </div>
            <div className="mt-[30px] flex justify-between">
              <button onClick={actions.obBack} className={btnBack}>← Back</button>
              <button onClick={goOverview} className="rounded-[11px] bg-gradient-to-r from-ac to-[#a7e36e] px-[26px] py-[13px] text-[13.5px] font-semibold leading-none text-[#0a0c10]">Enter Perf Lab →</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
