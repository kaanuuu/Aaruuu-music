import React from "react";
import {
  Music,
  Terminal,
  Radio,
  Play,
  Pause,
  SkipForward,
  SkipBack,
  RotateCcw,
  ListMusic,
  Shuffle,
  Repeat,
  MessageCircle,
  X,
  ShieldCheck,
  CheckCircle2,
  Send,
  Activity,
  HeartPulse,
} from "lucide-react";

export default function App() {
  const commands = [
    { cmd: "/start", desc: "Launch Aaruu Music interactive guide" },
    { cmd: "/play <song/URL>", desc: "Search & stream audio or add to queue" },
    { cmd: "/pause", desc: "Pause current playback" },
    { cmd: "/resume", desc: "Resume paused playback" },
    { cmd: "/replay", desc: "Replay current song from 0:00" },
    { cmd: "/skip", desc: "Skip to next queued track (Admins)" },
    { cmd: "/queue", desc: "Display real-time queue & up-next list" },
    { cmd: "/shuffle", desc: "Randomize tracks in queue (Admins)" },
    { cmd: "/stop", desc: "Stop playback & clear queue" },
    { cmd: "/clear", desc: "Clear all queued tracks" },
    { cmd: "/loop <off|track|queue>", desc: "Configure repeat playback mode" },
    { cmd: "/seek <seconds>", desc: "Jump to specific track timestamp" },
    { cmd: "/volume <1-100>", desc: "Adjust playback volume" },
    { cmd: "/nowplaying", desc: "Display active Rich Message player" },
    { cmd: "/settings", desc: "Inspect chat volume, loop, & config" },
    { cmd: "/ping", desc: "Check live bot latency & 24/7 uptime" },
    { cmd: "/stats", desc: "View users, groups, and playback metrics" },
    { cmd: "/broadcast [-user|-group] <msg>", desc: "Broadcast announcement (Owner)" },
    { cmd: "/block <user_id>", desc: "Permanently ban user from bot (Owner)" },
    { cmd: "/unblock <user_id>", desc: "Unban blocked user (Owner)" },
    { cmd: "/blocked", desc: "List all blocked users (Owner)" },
    { cmd: "/admincache", desc: "Reload chat administrator permissions" },
  ];

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans p-6 md:p-12 selection:bg-cyan-500 selection:text-white">
      <div className="max-w-4xl mx-auto space-y-8">
        {/* Header Banner */}
        <header className="flex flex-col md:flex-row md:items-center justify-between pb-6 border-b border-slate-800 gap-4">
          <div className="flex items-center space-x-4">
            <div className="w-12 h-12 rounded-xl bg-cyan-600/20 border border-cyan-500/30 flex items-center justify-center text-cyan-400">
              <Music className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
                Aaruu Music
                <span className="text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium">
                  Worker Mode
                </span>
              </h1>
              <p className="text-sm text-slate-400">
                Production Telegram Music Bot · Native Rich Message Player
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3 text-xs text-slate-300 bg-slate-900 border border-slate-800 px-4 py-2 rounded-xl">
            <HeartPulse className="w-4 h-4 text-emerald-400 animate-pulse" />
            <span>24/7 Active · 10m Keep-Alive Ping</span>
          </div>
        </header>

        {/* Telegram Rich Message Preview Card */}
        <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-6 md:p-8 space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Terminal className="w-4 h-4 text-cyan-400" />
              Telegram Native Rich Message UI Mockup
            </h2>
            <span className="text-xs text-slate-500 font-mono">sendRichMessage API</span>
          </div>

          {/* Reference Player Representation */}
          <div className="max-w-md mx-auto bg-slate-950 border border-slate-800 rounded-2xl p-5 shadow-2xl space-y-4">
            <div className="flex justify-between items-start border-b border-slate-800/80 pb-3">
              <div>
                <p className="font-bold text-white text-base">Aaruu Music</p>
                <p className="text-xs text-slate-400">Command requested by @telegram_user</p>
              </div>
              <span className="text-[10px] bg-cyan-950 text-cyan-300 px-2 py-0.5 rounded-full font-mono">
                AUDIO
              </span>
            </div>

            {/* Album Cover */}
            <div className="aspect-video w-full rounded-xl bg-gradient-to-tr from-cyan-900/40 via-slate-800 to-indigo-900/40 border border-slate-800 flex items-center justify-center overflow-hidden relative">
              <img
                src="https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
                alt="Track Cover"
                className="w-full h-full object-cover opacity-80"
              />
              <div className="absolute inset-0 bg-gradient-to-t from-slate-950/90 via-transparent to-transparent" />
              <div className="absolute bottom-3 left-3 right-3 text-left">
                <p className="font-semibold text-white text-sm truncate">Divine Melodies (Official Audio)</p>
                <p className="text-xs text-slate-300">Aaruu Studio · 3:06</p>
              </div>
            </div>

            {/* Progress Line */}
            <div className="font-mono text-xs text-center text-slate-300 tracking-wider bg-slate-900/80 py-2 rounded-xl border border-slate-800">
              2:25 ━━━━━━━━━━━●━━━━━━ 3:06
            </div>

            {/* Native Rich Message Compact Rounded Buttons */}
            <div className="space-y-2 pt-2 flex flex-col items-center">
              {/* Row 1: Prev | Pause | Skip */}
              <div className="flex items-center justify-center gap-2 w-full">
                <button
                  type="button"
                  className="bg-cyan-950/40 hover:bg-cyan-900/50 text-cyan-300 border border-cyan-500/30 text-xs py-1.5 px-3 rounded-xl font-medium flex items-center justify-center gap-1.5 transition shadow-sm"
                >
                  <SkipBack className="w-3.5 h-3.5" /> Prev
                </button>
                <button
                  type="button"
                  className="bg-cyan-950/40 hover:bg-cyan-900/50 text-cyan-300 border border-cyan-500/30 text-xs py-1.5 px-3 rounded-xl font-medium flex items-center justify-center gap-1.5 transition shadow-sm"
                >
                  <Pause className="w-3.5 h-3.5" /> Pause
                </button>
                <button
                  type="button"
                  className="bg-cyan-950/40 hover:bg-cyan-900/50 text-cyan-300 border border-cyan-500/30 text-xs py-1.5 px-3 rounded-xl font-medium flex items-center justify-center gap-1.5 transition shadow-sm"
                >
                  <SkipForward className="w-3.5 h-3.5" /> Skip
                </button>
              </div>

              {/* Row 2: Queue | Replay | Shuffle */}
              <div className="flex items-center justify-center gap-2 w-full">
                <button
                  type="button"
                  className="bg-cyan-950/40 hover:bg-cyan-900/50 text-cyan-300 border border-cyan-500/30 text-xs py-1.5 px-3 rounded-xl font-medium flex items-center justify-center gap-1.5 transition shadow-sm"
                >
                  <ListMusic className="w-3.5 h-3.5" /> ☷ Queue · 0
                </button>
                <button
                  type="button"
                  className="bg-cyan-950/40 hover:bg-cyan-900/50 text-cyan-300 border border-cyan-500/30 text-xs py-1.5 px-3 rounded-xl font-medium flex items-center justify-center gap-1.5 transition shadow-sm"
                >
                  <RotateCcw className="w-3.5 h-3.5" /> Replay
                </button>
                <button
                  type="button"
                  className="bg-cyan-950/40 hover:bg-cyan-900/50 text-cyan-300 border border-cyan-500/30 text-xs py-1.5 px-3 rounded-xl font-medium flex items-center justify-center gap-1.5 transition shadow-sm"
                >
                  <Shuffle className="w-3.5 h-3.5" /> Shuffle
                </button>
              </div>

              {/* Row 3: Loop | Autoplay */}
              <div className="flex items-center justify-center gap-2 w-full">
                <button
                  type="button"
                  className="bg-cyan-950/40 hover:bg-cyan-900/50 text-cyan-300 border border-cyan-500/30 text-xs py-1.5 px-3 rounded-xl font-medium flex items-center justify-center gap-1.5 transition shadow-sm"
                >
                  <Repeat className="w-3.5 h-3.5" /> Loop: OFF
                </button>
                <button
                  type="button"
                  className="bg-cyan-950/40 hover:bg-cyan-900/50 text-cyan-300 border border-cyan-500/30 text-xs py-1.5 px-3 rounded-xl font-medium flex items-center justify-center gap-1.5 transition shadow-sm"
                >
                  <Radio className="w-3.5 h-3.5" /> Autoplay: ON
                </button>
              </div>

              {/* Row 4: Support | Close */}
              <div className="flex items-center justify-center gap-2 w-full">
                <button
                  type="button"
                  className="bg-cyan-950/40 hover:bg-cyan-900/50 text-cyan-300 border border-cyan-500/30 text-xs py-1.5 px-3 rounded-xl font-medium flex items-center justify-center gap-1.5 transition shadow-sm"
                >
                  <MessageCircle className="w-3.5 h-3.5" /> Support
                </button>
                <button
                  type="button"
                  className="bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-200 border border-slate-800 text-xs py-1.5 px-3 rounded-xl font-medium flex items-center justify-center gap-1 transition shadow-sm"
                >
                  <X className="w-3.5 h-3.5" /> Close
                </button>
              </div>
            </div>
          </div>
        </section>

        {/* Command Reference */}
        <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-6 md:p-8 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-white flex items-center gap-2">
              <CheckCircle2 className="w-5 h-5 text-emerald-400" />
              Registered Telegram Commands Menu
            </h2>
            <span className="text-xs text-slate-500 font-mono">Autocompletes on &apos;/&apos;</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
            {commands.map((c) => (
              <div
                key={c.cmd}
                className="bg-slate-950/70 border border-slate-800/80 p-3 rounded-xl flex flex-col justify-center"
              >
                <code className="text-cyan-400 font-mono font-medium text-xs">
                  {c.cmd}
                </code>
                <span className="text-slate-400 text-xs mt-1">{c.desc}</span>
              </div>
            ))}
          </div>
        </section>

        {/* Deployment Info */}
        <footer className="text-xs text-slate-500 border-t border-slate-900 pt-6 flex flex-col md:flex-row justify-between items-center gap-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-emerald-500" />
            <span>Strict Bot-Only Architecture · 24/7 Keep-Alive Heartbeat Enabled</span>
          </div>
          <span className="font-mono">Railway Procfile: worker: bash start.sh</span>
        </footer>
      </div>
    </div>
  );
}

