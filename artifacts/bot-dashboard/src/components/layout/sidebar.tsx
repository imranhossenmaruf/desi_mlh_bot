import { Link, useLocation } from "wouter";
import { Terminal, Settings, FileCode, GitBranch, ShieldCheck } from "lucide-react";
import { useHealthCheck } from "@workspace/api-client-react";

export function Sidebar() {
  const [location] = useLocation();
  const { data: health } = useHealthCheck();

  const links = [
    { href: "/", label: "Overview", icon: GitBranch },
    { href: "/files", label: "Files", icon: FileCode },
    { href: "/config", label: "Config", icon: Settings },
    { href: "/logs", label: "Logs", icon: Terminal },
  ];

  return (
    <div className="w-64 border-r border-border bg-sidebar h-full flex flex-col">
      <div className="p-4 border-b border-border flex items-center gap-3">
        <div className="w-8 h-8 rounded-md bg-primary flex items-center justify-center text-primary-foreground">
          <Terminal size={18} />
        </div>
        <div>
          <h1 className="font-bold text-sm leading-tight text-foreground">Bot Dashboard</h1>
          <div className="flex items-center gap-1.5 mt-0.5">
            <div className={`w-2 h-2 rounded-full ${health?.status === 'ok' ? 'bg-emerald-500' : 'bg-red-500'}`} />
            <span className="text-xs text-muted-foreground font-mono">
              {health?.status === 'ok' ? 'System Online' : 'Connecting...'}
            </span>
          </div>
        </div>
      </div>
      
      <nav className="flex-1 p-3 space-y-1">
        {links.map((link) => {
          const isActive = location === link.href;
          return (
            <Link key={link.href} href={link.href}>
              <div
                className={`flex items-center gap-3 px-3 py-2 rounded-md transition-colors cursor-pointer text-sm font-medium ${
                  isActive 
                    ? "bg-secondary text-foreground" 
                    : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                }`}
              >
                <link.icon size={16} />
                {link.label}
              </div>
            </Link>
          );
        })}
      </nav>
      
      <div className="p-4 border-t border-border">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <ShieldCheck size={14} className="text-emerald-500" />
          <span>Secure Connection</span>
        </div>
      </div>
    </div>
  );
}
