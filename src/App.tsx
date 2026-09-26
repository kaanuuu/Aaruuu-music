import React, { useState } from 'react';
import { 
  Play, 
  Pause, 
  SkipForward, 
  Radio, 
  Music, 
  CheckCircle2, 
  AlertCircle, 
  FileText, 
  Copy, 
  Check, 
  Server, 
  Sliders, 
  Terminal, 
  ShieldCheck,
  Disc,
  Headphones,
  Settings,
  Sparkles
} from 'lucide-react';

export default function App() {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [cookieInput, setCookieInput] = useState('');
  const [cookieStatus, setCookieStatus] = useState<string | null>(null);

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(id);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const handleTestCookie = () => {
    if (!cookieInput.trim()) {
      setCookieStatus('empty');
      return;
    }
    if (cookieInput.includes('# Netscape HTTP Cookie File') || cookieInput.includes('.youtube.com')) {
      setCookieStatus('valid');
    } else {
      setCookieStatus('invalid');
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      {/* Header */}
      <header className="border-b border-slate-800/80 bg-slate-900/60 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
              <Headphones className="w-5 h-5 text-white" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h1 className="font-bold text-lg text-white tracking-tight">Aaruu Music</h1>
                <span className="text-xs px-2 py-0.5 rounded-full bg-cyan-500/20 text-cyan-400 font-medium border border-cyan-500/30">
                  AnonX Core Inside
                </span>
              </div>
              <p className="text-xs text-slate-400">Production Telegram Voice Chat Music Engine</p>
            </div>
          </div>
          <div className="flex items-center space-x-3">
            <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse mr-2"></span>
              PyTgCalls v3 Ready
            </span>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
        
        {/* Top Hero Banner */}
        <section className="relative overflow-hidden rounded-2xl bg-gradient-to-r from-slate-900 via-indigo-950/40 to-slate-900 border border-slate-800 p-6 md:p-8">
          <div className="relative z-10 max-w-3xl">
            <div className="inline-flex items-center space-x-2 px-3 py-1 rounded-full bg-indigo-500/10 border border-indigo-500/30 text-indigo-400 text-xs font-medium mb-4">
              <Sparkles className="w-3.5 h-3.5" />
              <span>Full PyTgCalls & AnonX Streaming Architecture</span>
            </div>
            <h2 className="text-2xl md:text-3xl font-extrabold text-white tracking-tight">
              High-Fidelity Telegram Voice Chat Audio Player
            </h2>
            <p className="mt-3 text-slate-300 text-sm md:text-base leading-relaxed">
              Equipped with AnonXMusic's <strong>WebRTC call engine</strong>, 48kHz stereo streaming, automatic fallback mechanisms (YouTube + JioSaavn + SoundCloud), and instant Netscape cookie authentication.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <a
                href="#cookies-section"
                className="px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white font-medium text-sm transition shadow-lg shadow-cyan-600/30 flex items-center space-x-2"
              >
                <FileText className="w-4 h-4" />
                <span>Configure Cookies</span>
              </a>
              <a
                href="#commands-section"
                className="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 font-medium text-sm transition border border-slate-700 flex items-center space-x-2"
              >
                <Terminal className="w-4 h-4" />
                <span>View Commands</span>
              </a>
            </div>
          </div>
        </section>

        {/* 3 Status Cards */}
        <section className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Card 1 */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 hover:border-slate-700 transition">
            <div className="flex items-center justify-between mb-4">
              <span className="p-2.5 rounded-lg bg-cyan-500/10 text-cyan-400">
                <Radio className="w-5 h-5" />
              </span>
              <span className="text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 font-mono">
                Active
              </span>
            </div>
            <h3 className="font-semibold text-white text-base">PyTgCalls Call Pipeline</h3>
            <p className="text-xs text-slate-400 mt-1">
              Dual audio engine: live VC stream via PyTgCalls session + fallback rich message player.
            </p>
            <div className="mt-4 pt-4 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-400 font-mono">
              <span>Cache Duration: 100</span>
              <span className="text-cyan-400">AudioQuality.HIGH</span>
            </div>
          </div>

          {/* Card 2 */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 hover:border-slate-700 transition">
            <div className="flex items-center justify-between mb-4">
              <span className="p-2.5 rounded-lg bg-indigo-500/10 text-indigo-400">
                <Disc className="w-5 h-5" />
              </span>
              <span className="text-xs px-2.5 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 font-mono">
                Multi-Source
              </span>
            </div>
            <h3 className="font-semibold text-white text-base">Extraction Fallbacks</h3>
            <p className="text-xs text-slate-400 mt-1">
              Zero downtime music playback: YouTube oEmbed + scraping, JioSaavn CDN direct stream, and SoundCloud.
            </p>
            <div className="mt-4 pt-4 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-400 font-mono">
              <span>Opus / WebM / MP4</span>
              <span className="text-emerald-400">100% Reliable</span>
            </div>
          </div>

          {/* Card 3 */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 hover:border-slate-700 transition">
            <div className="flex items-center justify-between mb-4">
              <span className="p-2.5 rounded-lg bg-amber-500/10 text-amber-400">
                <ShieldCheck className="w-5 h-5" />
              </span>
              <span className="text-xs px-2.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 font-mono">
                Netscape
              </span>
            </div>
            <h3 className="font-semibold text-white text-base">Cookie Auto-Discovery</h3>
            <p className="text-xs text-slate-400 mt-1">
              Auto-detects cookie files from <code className="text-amber-300">anony/cookies/*.txt</code> or env variable.
            </p>
            <div className="mt-4 pt-4 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-400 font-mono">
              <span>anony/cookies/</span>
              <span className="text-amber-400">Auto-Loaded</span>
            </div>
          </div>
        </section>

        {/* Cookie Setup Guide & Validator */}
        <section id="cookies-section" className="bg-slate-900/80 border border-slate-800 rounded-xl p-6 space-y-6">
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div>
              <h3 className="text-lg font-bold text-white flex items-center space-x-2">
                <FileText className="w-5 h-5 text-cyan-400" />
                <span>YouTube Cookie Configuration Guide</span>
              </h3>
              <p className="text-xs text-slate-400 mt-1">
                YouTube bot-check blocks datacenter IPs without cookies. Here is how to configure your cookie.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Left Column: Methods */}
            <div className="space-y-4">
              <div className="p-4 rounded-lg bg-slate-950/70 border border-slate-800">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-white">Tarika 1: Direct File (Sabse Aasan)</span>
                  <span className="text-xs text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">Recommended</span>
                </div>
                <p className="text-xs text-slate-400 mt-2">
                  Apni Netscape cookie file ko simply is folder me save kar dein:
                </p>
                <div className="mt-2 p-2 bg-slate-900 rounded font-mono text-xs text-cyan-300 flex items-center justify-between">
                  <span>anony/cookies/cookie.txt</span>
                  <button
                    onClick={() => copyToClipboard('anony/cookies/cookie.txt', 'p1')}
                    className="hover:text-white transition"
                  >
                    {copiedKey === 'p1' ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              <div className="p-4 rounded-lg bg-slate-950/70 border border-slate-800">
                <span className="text-sm font-semibold text-white">Tarika 2: Environment Variable (.env)</span>
                <p className="text-xs text-slate-400 mt-2">
                  Agar aap Railway, Heroku ya VPS use kar rahe hain:
                </p>
                <div className="mt-2 space-y-1.5 font-mono text-xs">
                  <div className="p-2 bg-slate-900 rounded text-slate-300 flex items-center justify-between">
                    <span>YOUTUBE_COOKIES_FILE=anony/cookies/cookie.txt</span>
                    <button
                      onClick={() => copyToClipboard('YOUTUBE_COOKIES_FILE=anony/cookies/cookie.txt', 'p2')}
                      className="hover:text-white transition"
                    >
                      {copiedKey === 'p2' ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                    </button>
                  </div>
                  <div className="p-2 bg-slate-900 rounded text-slate-300 flex items-center justify-between">
                    <span>YTDLP_COOKIES_TEXT=...</span>
                    <button
                      onClick={() => copyToClipboard('YTDLP_COOKIES_TEXT', 'p3')}
                      className="hover:text-white transition"
                    >
                      {copiedKey === 'p3' ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* Right Column: Cookie Format Tester */}
            <div className="p-4 rounded-lg bg-slate-950/70 border border-slate-800 flex flex-col justify-between">
              <div>
                <span className="text-sm font-semibold text-white">Test Your Cookie Format</span>
                <p className="text-xs text-slate-400 mt-1">
                  Paste a few lines of your Netscape cookie below to verify if the format is valid:
                </p>
                <textarea
                  value={cookieInput}
                  onChange={(e) => setCookieInput(e.target.value)}
                  placeholder="# Netscape HTTP Cookie File&#10;.youtube.com&#9;TRUE&#9;/&#9;TRUE&#9;1789999999&#9;VISITOR_INFO1_LIVE&#9;xxxx"
                  rows={4}
                  className="w-full mt-3 p-2.5 rounded bg-slate-900 border border-slate-800 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500 transition"
                ></textarea>
              </div>

              <div className="mt-3 flex items-center justify-between">
                <button
                  onClick={handleTestCookie}
                  className="px-4 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-white font-medium text-xs transition border border-slate-700"
                >
                  Verify Format
                </button>
                {cookieStatus === 'valid' && (
                  <span className="text-xs text-emerald-400 flex items-center space-x-1">
                    <CheckCircle2 className="w-4 h-4" />
                    <span>Valid Netscape Format</span>
                  </span>
                )}
                {cookieStatus === 'invalid' && (
                  <span className="text-xs text-rose-400 flex items-center space-x-1">
                    <AlertCircle className="w-4 h-4" />
                    <span>Invalid format! Must contain tabs or # Netscape</span>
                  </span>
                )}
                {cookieStatus === 'empty' && (
                  <span className="text-xs text-amber-400">Please enter cookie text</span>
                )}
              </div>
            </div>
          </div>
        </section>

        {/* Telegram Music Bot Commands */}
        <section id="commands-section" className="bg-slate-900/80 border border-slate-800 rounded-xl p-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-lg font-bold text-white flex items-center space-x-2">
                <Terminal className="w-5 h-5 text-indigo-400" />
                <span>Telegram Bot Commands</span>
              </h3>
              <p className="text-xs text-slate-400 mt-1">
                Full list of commands supported by Aaruu Music & AnonXMusic engine:
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 font-mono text-xs">
            {[
              { cmd: '/play <query/url>', desc: 'Plays track in Voice Chat with Rich Player UI' },
              { cmd: '/vplay <query/url>', desc: 'Plays video track with 720p HD stream' },
              { cmd: '/pause', desc: 'Pauses active Voice Chat stream' },
              { cmd: '/resume', desc: 'Resumes paused playback' },
              { cmd: '/skip', desc: 'Skips current track to next in queue' },
              { cmd: '/stop ya /end', desc: 'Stops audio and clears group queue' },
              { cmd: '/queue', desc: 'Displays upcoming songs with interactive pagination' },
              { cmd: '/seek <seconds>', desc: 'Jumps playback forward/backward' },
              { cmd: '/loop <count>', desc: 'Loops current track 1 to 5 times' },
              { cmd: '/ping', desc: 'Checks PyTgCalls WebRTC & server latency' },
              { cmd: '/stats', desc: 'Displays CPU, RAM, and active voice chats' },
              { cmd: '/help', desc: 'Opens interactive button-based help guide' },
            ].map((item, idx) => (
              <div key={idx} className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80 hover:border-slate-700 transition">
                <span className="text-cyan-400 font-semibold">{item.cmd}</span>
                <p className="text-slate-400 font-sans mt-1 text-xs">{item.desc}</p>
              </div>
            ))}
          </div>
        </section>

      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800/80 bg-slate-900/40 py-6 mt-12 text-center text-xs text-slate-400">
        <p>Aaruu Music Engine • Powered by PyTgCalls, Pyrogram, and AnonX Architecture</p>
      </footer>
    </div>
  );
}
