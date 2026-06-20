// src/perflab/screens/OverviewScreen.tsx
import type { ReactNode } from "react";
import { usePerfLab } from "../store";
import { Card, ReadinessRing, SectionLabel, SyncChip, Track } from "../ui";
import { buildCheckin, COLORS, DAYS, DAY_COUNT, readinessColor, readinessWord } from "../sim";

function StatCol({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">{label}</div>
      <div className="mt-2 font-mono text-[18px] font-semibold leading-none text-ink">{children}</div>
    </div>
  );
}

export function OverviewScreen() {
  const { state, actions } = usePerfLab();
  const ci = state.checkin;
  const ciR = buildCheckin(ci);
  const ciReady = ciR.readiness;
  const ciColor = readinessColor(ciReady);
  const ciWord = readinessWord(ciReady);

  const N = DAY_COUNT;
  const todayD = DAYS[N - 1];
  const ovVal = ci.done ? ciReady : todayD.readiness;
  const ovColor = readinessColor(ovVal);
  const ovWord = readinessWord(ovVal);

  const ovDays = DAYS.slice(Math.max(0, N - 14));
  const oN = ovDays.length;
  const oW = 300, opad = 6, oTop = 8, oBot = 58;
  const ox = (i: number) => opad + (i / (oN - 1)) * (oW - 2 * opad);
  const oy = (r: number) => oBot - ((r - 20) / 80) * (oBot - oTop);
  const ovLine = ovDays.map((d, i) => `${ox(i).toFixed(1)},${oy(d.readiness).toFixed(1)}`).join(" ");
  let ovArea = `M ${ox(0).toFixed(1)} ${oBot}`;
  ovDays.forEach((d, i) => (ovArea += ` L ${ox(i).toFixed(1)} ${oy(d.readiness).toFixed(1)}`));
  ovArea += ` L ${ox(oN - 1).toFixed(1)} ${oBot} Z`;
  const ovDiff = ovVal - ovDays[0].readiness;
  const ovDelta = `${ovDiff >= 0 ? "+" : ""}${ovDiff} vs 2w ago`;

  // Insights derived from today's state
  const ents = (o: Record<string, number>) => Object.entries(o).sort((a, b) => b[1] - a[1]);
  const topT = ents(todayD.T)[0];
  const topF = ents(todayD.F)[0];
  const alerts: { dot: string; title: string; desc: string }[] = [];
  if (topT[1] >= 45) alerts.push({ dot: COLORS.hot, title: `${topT[0]} load high (${topT[1]})`, desc: "Local stress elevated — avoid high-impact work until it settles." });
  else if (topT[1] >= 30) alerts.push({ dot: COLORS.warn, title: `${topT[0]} load climbing (${topT[1]})`, desc: "Monitor — keep impact volume capped this week." });
  if (topF[1] >= 45) alerts.push({ dot: COLORS.hot, title: `${topF[0]} fatigue elevated (${topF[1]})`, desc: "Allow full recovery between quality sessions." });
  alerts.push(
    todayD.readiness >= 55
      ? { dot: COLORS.lime, title: `Readiness ${todayD.readiness} · moderate`, desc: "Hold intensity; a full quality session is viable within ~24h." }
      : { dot: COLORS.hot, title: `Readiness low (${todayD.readiness})`, desc: "Prioritise easy volume or recovery today." },
  );
  alerts.push({ dot: COLORS.good, title: "Training load optimal · ACWR 1.08", desc: "Inside the 0.8–1.3 sweet spot — safe to keep progressing." });
  alerts.push({ dot: COLORS.teal, title: "Adaptation signal rising", desc: "Recent endurance load is actively building your aerobic base." });

  const btnGhost = "rounded-[9px] border border-white/10 bg-white/[0.04] px-[14px] py-[9px] text-[12.5px] font-semibold leading-none text-soft";

  return (
    <section className="flex flex-col gap-[18px] px-[30px] pb-9 pt-[26px]">
      <header className="flex items-start justify-between gap-5">
        <div>
          <h1 className="m-0 text-[25px] font-bold leading-none tracking-[-0.02em] text-ink">Good morning, Alex</h1>
          <p className="m-0 mt-[9px] text-[13.5px] font-medium leading-[1.5] text-[#7c818c]">Tuesday · 17 Jun &nbsp;·&nbsp; Mid-base block, week 3 of 7</p>
        </div>
        <div className="flex items-center gap-[9px]">
          <SyncChip label="Synced 2h ago" />
          <button onClick={actions.openCheckin} className={btnGhost}>Check in</button>
          <button onClick={actions.openLog} className="rounded-[9px] bg-ink px-[15px] py-[9px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Log workout</button>
        </div>
      </header>

      {/* Goal race + this-morning */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.3fr_1fr]">
        <Card
          onClick={() => actions.setScreen("race")}
          className="flex items-center justify-between gap-4"
          style={{ background: "radial-gradient(120% 140% at 100% 0%,#11321f,#111419 55%)" }}
        >
          <div>
            <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-ac">Goal race · {state.race.dateLabel}</div>
            <div className="mt-[10px] text-[19px] font-bold leading-none tracking-[-0.01em] text-ink">{state.race.name}</div>
            <div className="mt-[9px] text-[12px] font-medium leading-none text-mute">
              Goal 2:55:00 · predicted <span className="text-teal">2:53:40</span> <span className="text-good">(−1:20 ahead of goal)</span>
            </div>
          </div>
          <div className="flex-none text-right">
            <div className="font-mono text-[42px] font-semibold leading-[0.9] tracking-[-0.02em] text-ink">{state.race.daysToGo}</div>
            <div className="mt-[5px] font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.14em] text-faint">days to go →</div>
          </div>
        </Card>

        <Card className="flex flex-col gap-[14px]">
          <div className="flex items-center justify-between">
            <SectionLabel>This morning</SectionLabel>
            <div className="flex items-center gap-[9px]">
              <span className="font-mono text-[13px] font-semibold leading-none" style={{ color: ciColor }}>{ciReady} {ciWord}</span>
              <button onClick={actions.openCheckin} className="rounded-[8px] bg-ac px-[11px] py-[7px] text-[11px] font-semibold leading-none text-[#0a0c10]">Check in →</button>
            </div>
          </div>
          <div className="grid grid-cols-4 gap-3">
            {[
              ["HRV", `${ci.hrv} ms`],
              ["Sleep", `${ci.sleepH} h`],
              ["Rest HR", `${ci.rhr} bpm`],
              ["Soreness", ci.soreness.charAt(0).toUpperCase() + ci.soreness.slice(1)],
            ].map(([l, v]) => (
              <div key={l}>
                <div className="font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.1em] text-faint">{l}</div>
                <div className="mt-[6px] font-mono text-[15px] font-semibold leading-none text-ink">{v}</div>
              </div>
            ))}
          </div>
          <div className="text-[10.5px] font-medium leading-none text-dim">{ci.done ? "Logged this morning" : "Tap to log this morning"} · feeds readiness &amp; twin</div>
        </Card>
      </div>

      {/* Today + right stack */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
        <Card className="p-6">
          <div className="flex items-start gap-6">
            <div className="w-[300px] flex-none">
              <div className="flex items-center gap-4">
                <ReadinessRing value={ovVal} color={ovColor} size={96} inner={74} valueClassName="text-[29px]" />
                <div>
                  <SectionLabel className="text-faint">Readiness</SectionLabel>
                  <div className="mt-2 text-[17px] font-bold leading-none" style={{ color: ovColor }}>{ovWord}</div>
                  <div className="mt-[7px] text-[11px] font-medium leading-none text-good">{ovDelta}</div>
                </div>
              </div>
              <div className="mt-4 font-mono text-[9px] font-semibold uppercase leading-none tracking-[0.14em] text-dim">Last 14 days</div>
              <svg viewBox="0 0 300 70" preserveAspectRatio="none" className="mt-2 block h-[56px] w-full overflow-visible">
                <defs>
                  <linearGradient id="ovg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="rgba(198,241,53,.26)" />
                    <stop offset="1" stopColor="rgba(198,241,53,0)" />
                  </linearGradient>
                </defs>
                <path d={ovArea} fill="url(#ovg)" />
                <polyline points={ovLine} fill="none" stroke="var(--ac)" strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
                <circle cx={ox(oN - 1)} cy={oy(ovVal)} r="3.5" fill="var(--ac)" />
              </svg>
            </div>
            <div className="flex-1">
              <div className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.14em] text-ac">Recommended today</div>
              <div className="mt-[9px] text-[22px] font-bold leading-[1.1] text-ink">Tempo intervals · Zone 3</div>
              <div className="mt-[9px] max-w-[380px] text-[13px] font-medium leading-[1.5] text-mute">Readiness is moderate — hold quality but cap volume. 5 × 6 min @ 4:30/km, 90 s float.</div>
              <div className="mt-4 flex gap-[10px]">
                <button onClick={actions.openSession} className="rounded-[9px] bg-gradient-to-r from-ac to-[#a7e36e] px-4 py-[10px] text-[12.5px] font-semibold leading-none text-[#0a0c10]">Start session</button>
                <button onClick={() => actions.setScreen("planning")} className={btnGhost}>View week</button>
              </div>
              <div className="mt-5 grid grid-cols-4 gap-[18px] border-t border-white/[0.06] pt-[18px]">
                <StatCol label="Duration">~52 min</StatCol>
                <StatCol label="Target pace">4:30 <span className="text-[11px] text-faint">/km</span></StatCol>
                <StatCol label="Session load">61</StatCol>
                <StatCol label="Readiness after">64 <span className="text-[13px] text-dim">→</span> <span className="text-warn">48</span></StatCol>
              </div>
              <div className="mt-4 flex items-start gap-[9px] rounded-[11px] border border-ac/[0.18] bg-ac/[0.05] px-[13px] py-[11px]">
                <span className="text-[12px] font-medium leading-[1.5] text-mute">Readiness is moderate, so today holds quality at tempo and caps volume — protecting Friday's threshold session. Knee load (40) keeps impact modest.</span>
              </div>
            </div>
          </div>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <div className="flex items-center justify-between">
              <SectionLabel className="text-faint">Training load</SectionLabel>
              <span className="font-mono text-[11px] font-semibold leading-none text-good">optimal</span>
            </div>
            <div className="mt-3 flex items-end gap-2">
              <span className="font-mono text-[30px] font-semibold leading-none text-ink">1.08</span>
              <span className="mb-1 text-[11px] font-medium leading-none text-faint">ACWR · 7d/28d</span>
            </div>
            <div className="relative mt-3 h-[6px] overflow-hidden rounded-full bg-white/[0.07]">
              <div className="absolute bottom-0 left-[40%] right-[25%] top-0 bg-good/25" />
              <div className="absolute left-[54%] top-[-2px] h-[10px] w-[3px] rounded-[2px] bg-ac" />
            </div>
            <div className="mt-[7px] font-mono text-[10px] leading-none text-dim">sweet spot 0.8–1.3</div>
          </Card>
          <Card>
            <div className="flex items-center justify-between">
              <SectionLabel className="text-faint">Habit</SectionLabel>
              <span className="font-mono text-[11px] font-semibold leading-none text-ac">5-day streak</span>
            </div>
            <div className="mt-3 flex items-end gap-2">
              <span className="font-mono text-[30px] font-semibold leading-none text-ink">45%</span>
              <span className="mb-1 text-[11px] font-medium leading-none text-faint">adherence</span>
            </div>
            <Track pct={45} className="mt-3 h-[6px]" />
          </Card>
        </div>
      </div>

      {/* Recent + twin snapshot */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <SectionLabel className="mb-4">Recent activity</SectionLabel>
          <div className="flex flex-col gap-[2px]">
            {[
              [COLORS.hot, "Long run · 22 km", "Sun · Z2 endurance", "load 78"],
              [COLORS.warn, "Threshold · 4 × 8 min", "Fri · Z4", "load 61"],
              [COLORS.good, "Recovery jog · 8 km", "Thu · Z1", "load 24"],
            ].map(([dot, t, sub, load], i) => (
              <div key={i} className="flex items-center gap-[13px] border-b border-white/[0.05] py-[11px] last:border-0">
                <div className="h-[9px] w-[9px] flex-none rounded-full" style={{ background: dot }} />
                <div className="flex-1">
                  <div className="text-[13px] font-semibold leading-none text-[#e6e8ec]">{t}</div>
                  <div className="mt-1 text-[11px] font-medium leading-none text-faint">{sub}</div>
                </div>
                <span className="font-mono text-[11px] font-semibold leading-none" style={{ color: dot }}>{load}</span>
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <SectionLabel>Twin snapshot</SectionLabel>
            <button onClick={() => actions.setScreen("twin")} className="text-[11px] font-medium leading-none text-teal">Open twin →</button>
          </div>
          <div className="grid grid-cols-2 gap-x-[22px] gap-y-[14px]">
            <Snap label="Aerobic" value="320" />
            <Snap label="VO₂max" value="58.4" color="text-teal" />
            <Snap label="Mean fatigue" value="33" color="text-warn" />
            <Snap label="Peak tissue" value={<>40 <span className="text-[11px] text-faint">knee</span></>} color="text-warn" />
          </div>
        </Card>
      </div>

      <Card>
        <div className="mb-[14px] flex items-center justify-between">
          <SectionLabel>Insights</SectionLabel>
          <span className="text-[11px] font-medium leading-none text-dim">auto-generated from S(t)</span>
        </div>
        <div className="flex flex-col gap-[2px]">
          {alerts.map((a, i) => (
            <div key={i} className="flex items-start gap-3 border-b border-white/[0.05] py-[11px] last:border-0">
              <span className="mt-[3px] h-[9px] w-[9px] flex-none rounded-full" style={{ background: a.dot }} />
              <div>
                <div className="text-[13px] font-semibold leading-none text-[#e6e8ec]">{a.title}</div>
                <div className="mt-1 text-[11.5px] font-medium leading-[1.5] text-[#7c818c]">{a.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </section>
  );
}

function Snap({ label, value, color = "text-ink" }: { label: string; value: ReactNode; color?: string }) {
  return (
    <div>
      <div className="text-[11px] font-medium leading-none text-mute">{label}</div>
      <div className={`mt-[5px] font-mono text-[22px] font-semibold leading-none ${color}`}>{value}</div>
    </div>
  );
}
