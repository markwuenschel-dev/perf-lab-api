import { useEffect, useMemo, useState } from "react";
import {
  createPlanningBlock,
  listPlannedSessions,
  listPlanningBlocks,
  updatePlannedSession,
} from "../api/perfLabClient";
import { useAuth } from "../auth/useAuth";
import type {
  ApiError,
  BlockCreateRequest,
  BlockGoal,
  BlockRead,
  PlannedSessionRead,
  SessionStatus,
} from "../types";
import { BLOCK_GOALS } from "../trainingGoals";
import { toApiError } from "./twin/stateUtils";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

function ymd(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function PlanningPanel() {
  const { token, isAuthenticated } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [blocks, setBlocks] = useState<BlockRead[]>([]);
  const [sessions, setSessions] = useState<PlannedSessionRead[]>([]);

  const [goal, setGoal] = useState<BlockGoal>("Strength");
  const [startDate, setStartDate] = useState<string>(ymd(new Date()));
  const [durationWeeks, setDurationWeeks] = useState<number>(8);
  const [sessionsPerWeek, setSessionsPerWeek] = useState<number>(3);
  const [deloadEvery, setDeloadEvery] = useState<number>(4);
  const [benchmarkEvery, setBenchmarkEvery] = useState<number>(4);

  const dateWindow = useMemo(() => {
    const now = new Date();
    const from = new Date(now);
    from.setDate(now.getDate() - 7);
    const to = new Date(now);
    to.setDate(now.getDate() + 28);
    return { from: ymd(from), to: ymd(to) };
  }, []);

  async function loadAll(): Promise<void> {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [b, s] = await Promise.all([
        listPlanningBlocks(token),
        listPlannedSessions(token, { start_date: dateWindow.from, end_date: dateWindow.to }),
      ]);
      setBlocks(b);
      setSessions(s);
    } catch (err: unknown) {
      setError(toApiError(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAll();
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps

  async function createBlock(): Promise<void> {
    if (!token) return;
    setLoading(true);
    setError(null);
    const body: BlockCreateRequest = {
      goal,
      start_date: startDate,
      duration_weeks: durationWeeks,
      sessions_per_week: sessionsPerWeek,
      deload_every_n_weeks: deloadEvery,
      benchmark_every_n_weeks: benchmarkEvery,
    };
    try {
      await createPlanningBlock(body, token);
      await loadAll();
    } catch (err: unknown) {
      setError(toApiError(err));
      setLoading(false);
    }
  }

  async function updateStatus(sessionId: number, status: SessionStatus): Promise<void> {
    if (!token) return;
    try {
      await updatePlannedSession(sessionId, { status }, token);
      await loadAll();
    } catch (err: unknown) {
      setError(toApiError(err));
    }
  }

  async function reschedulePlusOne(session: PlannedSessionRead): Promise<void> {
    if (!token) return;
    const d = new Date(`${session.scheduled_date}T00:00:00`);
    d.setDate(d.getDate() + 1);
    try {
      await updatePlannedSession(
        session.id,
        { status: "rescheduled", scheduled_date: ymd(d) },
        token,
      );
      await loadAll();
    } catch (err: unknown) {
      setError(toApiError(err));
    }
  }

  if (!isAuthenticated || !token) {
    return (
      <Card className="border-white/10 bg-zinc-900/70 backdrop-blur-2xl">
        <CardContent className="p-6 text-zinc-200">Sign in to access block planning and session calendar.</CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <Card className="border-white/10 bg-zinc-900/70 backdrop-blur-2xl">
        <CardContent className="p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-white">Create Planning Block</h3>
            <Button variant="outline" onClick={() => void loadAll()} disabled={loading}>
              Refresh
            </Button>
          </div>
          {error && (
            <div className="rounded-xl border border-rose-400/60 bg-rose-950/40 p-3 text-sm text-rose-200">
              {error.message}
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <Label className="text-zinc-300">Goal</Label>
              <Select value={goal} onValueChange={(v) => setGoal(v as BlockGoal)}>
                <SelectTrigger className="bg-black/50 border-white/20 text-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {BLOCK_GOALS.map(({ value, label }) => (
                    <SelectItem key={value} value={value}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-zinc-300">Start Date</Label>
              <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="bg-black/50 border-white/20 text-white" />
            </div>
            <div>
              <Label className="text-zinc-300">Duration (weeks)</Label>
              <Input type="number" min={1} max={24} value={durationWeeks} onChange={(e) => setDurationWeeks(Number(e.target.value))} className="bg-black/50 border-white/20 text-white" />
            </div>
            <div>
              <Label className="text-zinc-300">Sessions / Week</Label>
              <Input type="number" min={1} max={7} value={sessionsPerWeek} onChange={(e) => setSessionsPerWeek(Number(e.target.value))} className="bg-black/50 border-white/20 text-white" />
            </div>
            <div>
              <Label className="text-zinc-300">Deload Every N Weeks</Label>
              <Input type="number" min={1} max={12} value={deloadEvery} onChange={(e) => setDeloadEvery(Number(e.target.value))} className="bg-black/50 border-white/20 text-white" />
            </div>
            <div>
              <Label className="text-zinc-300">Benchmark Every N Weeks</Label>
              <Input type="number" min={1} max={12} value={benchmarkEvery} onChange={(e) => setBenchmarkEvery(Number(e.target.value))} className="bg-black/50 border-white/20 text-white" />
            </div>
          </div>
          <Button onClick={() => void createBlock()} disabled={loading} className="bg-neon-cyan text-black font-semibold">
            {loading ? "Saving..." : "Create Block"}
          </Button>
        </CardContent>
      </Card>

      <Card className="border-white/10 bg-zinc-900/70 backdrop-blur-2xl">
        <CardContent className="p-6 space-y-4">
          <h3 className="text-lg font-semibold text-white">Blocks</h3>
          <div className="flex flex-wrap gap-3">
            {blocks.length === 0 && <div className="text-sm text-zinc-400">No blocks yet.</div>}
            {blocks.map((b) => (
              <div key={b.id} className="rounded-xl border border-white/10 bg-black/30 p-3 min-w-[240px]">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-zinc-100 font-medium">{b.goal}</span>
                  <Badge variant="outline" className="text-zinc-300 border-zinc-600">{b.status}</Badge>
                </div>
                <div className="text-xs text-zinc-400 mt-2">
                  {b.start_date} to {b.end_date ?? "—"} • {b.duration_weeks}w • {b.sessions_per_week}/week
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card className="border-white/10 bg-zinc-900/70 backdrop-blur-2xl">
        <CardContent className="p-6 space-y-4">
          <h3 className="text-lg font-semibold text-white">Session Calendar (MVP List)</h3>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-zinc-300">Date</TableHead>
                <TableHead className="text-zinc-300">Category</TableHead>
                <TableHead className="text-zinc-300">Status</TableHead>
                <TableHead className="text-zinc-300">Flags</TableHead>
                <TableHead className="text-zinc-300">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sessions.map((s) => (
                <TableRow key={s.id}>
                  <TableCell className="text-zinc-200">{s.scheduled_date}</TableCell>
                  <TableCell className="text-zinc-100">{s.category}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className="border-zinc-600 text-zinc-300">{s.status}</Badge>
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      {s.is_deload && <Badge className="bg-amber-700/50 text-amber-100">deload</Badge>}
                      {s.is_benchmark && <Badge className="bg-violet-700/50 text-violet-100">benchmark</Badge>}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button size="xs" variant="outline" onClick={() => void updateStatus(s.id, "completed")}>Complete</Button>
                      <Button size="xs" variant="outline" onClick={() => void updateStatus(s.id, "skipped")}>Skip</Button>
                      <Button size="xs" variant="outline" onClick={() => void reschedulePlusOne(s)}>+1 day</Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {sessions.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="text-zinc-400">No sessions in current window.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

