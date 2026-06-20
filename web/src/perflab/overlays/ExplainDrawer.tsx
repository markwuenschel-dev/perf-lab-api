// src/perflab/overlays/ExplainDrawer.tsx
import { usePerfLab } from "../store";
import { buildExplain, DAYS, DAY_COUNT } from "../sim";
import { CloseBtn } from "./LogWorkoutModal";

export function ExplainDrawer() {
  const { state, actions } = usePerfLab();
  if (!state.explainOpen || !state.explainKey) return null;

  const N = DAY_COUNT;
  let di = state.twinDayIdx;
  if (di == null || di > N - 1) di = N - 1;
  if (di < 0) di = 0;
  const ex = buildExplain(state.explainKey, DAYS[di]);

  return (
    <div onClick={actions.closeExplain} className="fixed inset-0 z-[65] flex justify-end backdrop-blur-[3px]" style={{ background: "rgba(4,5,8,.55)" }}>
      <div onClick={(e) => e.stopPropagation()} className="flex h-full w-[440px] max-w-full flex-col overflow-auto border-l border-white/[0.08] bg-surface shadow-[-30px_0_80px_-30px_rgba(0,0,0,.7)]">
        <div className="flex items-start justify-between border-b border-white/[0.06] px-6 py-[22px]">
          <div>
            <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">Why this value</div>
            <div className="mt-[9px] text-[19px] font-bold leading-none text-ink">{ex.label}</div>
            <div className="mt-[11px] flex items-baseline gap-[7px]">
              <span className="font-mono text-[40px] font-semibold leading-none" style={{ color: ex.accent }}>{ex.value}</span>
              <span className="text-[12px] font-medium leading-none text-faint">{ex.unit}</span>
            </div>
          </div>
          <CloseBtn onClick={actions.closeExplain} />
        </div>
        <div className="flex flex-col gap-[22px] px-6 py-[22px]">
          <div className="text-[13px] font-medium leading-[1.6] text-mute">{ex.def}</div>
          <div>
            <div className="mb-3 font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">{ex.driversLabel}</div>
            <div className="flex flex-col gap-[2px]">
              {ex.drivers.map((d, i) => (
                <div key={i} className="flex items-center justify-between gap-3 border-b border-white/[0.05] py-[11px] last:border-0">
                  <div>
                    <div className="text-[13px] font-semibold leading-none text-[#e6e8ec]">{d.n}</div>
                    <div className="mt-1 text-[11px] font-medium leading-none text-faint">{d.d}</div>
                  </div>
                  <span className="font-mono text-[15px] font-semibold leading-none" style={{ color: d.color }}>{d.delta}</span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="mb-3 font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-[#8b919c]">How it flows · D(t) → S(t)</div>
            <div className="rounded-[12px] border border-white/[0.07] bg-white/[0.02] p-4 text-center font-mono text-[15px] font-semibold leading-[1.4] text-soft">{ex.flow}</div>
            <div className="mt-3 text-[12px] font-medium leading-[1.6] text-[#7c818c]">{ex.note}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
