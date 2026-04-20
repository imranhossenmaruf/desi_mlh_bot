import React, { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Github, RefreshCw, UploadCloud, GitBranch } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { useQueryClient } from "@tanstack/react-query";

import { 
  useGetGitHubStatus, 
  useConnectGitHub, 
  useSyncFromGitHub, 
  usePushToGitHub,
  getGetGitHubStatusQueryKey 
} from "@workspace/api-client-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";

const connectSchema = z.object({
  repoUrl: z.string().url("Must be a valid URL"),
  token: z.string().min(1, "Token is required"),
  branch: z.string().min(1, "Branch is required").default("main"),
});

type ConnectValues = z.infer<typeof connectSchema>;

export default function Home() {
  const queryClient = useQueryClient();
  const { data: status, isLoading: isStatusLoading } = useGetGitHubStatus();
  const connectMutation = useConnectGitHub();
  const syncMutation = useSyncFromGitHub();
  const pushMutation = usePushToGitHub();

  const [pushDialogOpen, setPushDialogOpen] = useState(false);
  const [commitMessage, setCommitMessage] = useState("");

  const form = useForm<ConnectValues>({
    resolver: zodResolver(connectSchema),
    defaultValues: {
      repoUrl: "",
      token: "",
      branch: "main",
    }
  });

  const onConnect = (data: ConnectValues) => {
    connectMutation.mutate({ data }, {
      onSuccess: (res) => {
        if (res.success) {
          toast.success(res.message);
          queryClient.invalidateQueries({ queryKey: getGetGitHubStatusQueryKey() });
        } else {
          toast.error(res.message);
        }
      },
      onError: (err) => {
        toast.error("Failed to connect to GitHub");
      }
    });
  };

  const onSync = () => {
    syncMutation.mutate(undefined, {
      onSuccess: (res) => {
        if (res.success) {
          toast.success(res.message);
          queryClient.invalidateQueries({ queryKey: getGetGitHubStatusQueryKey() });
        } else {
          toast.error(res.message);
        }
      },
      onError: () => toast.error("Failed to sync from GitHub")
    });
  };

  const onPush = () => {
    if (!commitMessage) return;
    pushMutation.mutate({ data: { commitMessage } }, {
      onSuccess: (res) => {
        if (res.success) {
          toast.success(res.message);
          setPushDialogOpen(false);
          setCommitMessage("");
          queryClient.invalidateQueries({ queryKey: getGetGitHubStatusQueryKey() });
        } else {
          toast.error(res.message);
        }
      },
      onError: () => toast.error("Failed to push to GitHub")
    });
  };

  return (
    <div className="p-8 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-foreground">Overview</h1>
        <p className="text-muted-foreground mt-1">Manage your bot's codebase and connection.</p>
      </div>

      {isStatusLoading ? (
        <div className="h-40 flex items-center justify-center text-muted-foreground">Loading status...</div>
      ) : status?.connected ? (
        <Card className="border-border bg-card">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Github className="w-5 h-5" />
              Repository Connected
            </CardTitle>
            <CardDescription>Your bot is synced with {status.repoName || "GitHub"}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="space-y-1">
                <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">Repository</span>
                <p className="font-mono text-sm truncate">{status.repoName || "Unknown"}</p>
              </div>
              <div className="space-y-1">
                <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">Branch</span>
                <p className="font-mono text-sm flex items-center gap-1">
                  <GitBranch className="w-3 h-3 text-primary" /> {status.branch || "main"}
                </p>
              </div>
              <div className="space-y-1">
                <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">Last Sync</span>
                <p className="font-mono text-sm">
                  {status.lastSync ? formatDistanceToNow(new Date(status.lastSync), { addSuffix: true }) : "Never"}
                </p>
              </div>
              <div className="space-y-1">
                <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">Last Push</span>
                <p className="font-mono text-sm">
                  {status.lastPush ? formatDistanceToNow(new Date(status.lastPush), { addSuffix: true }) : "Never"}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-3 pt-4 border-t border-border">
              <Button onClick={onSync} disabled={syncMutation.isPending} variant="secondary" className="gap-2">
                <RefreshCw className={`w-4 h-4 ${syncMutation.isPending ? "animate-spin" : ""}`} />
                Pull Latest
              </Button>
              <Button onClick={() => setPushDialogOpen(true)} disabled={pushMutation.isPending} className="gap-2">
                <UploadCloud className="w-4 h-4" />
                Push Changes
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card className="border-border bg-card">
          <CardHeader>
            <CardTitle>Connect Repository</CardTitle>
            <CardDescription>Connect your bot's GitHub repository to enable code syncing.</CardDescription>
          </CardHeader>
          <CardContent>
            <Form {...form}>
              <form onSubmit={form.handleSubmit(onConnect)} className="space-y-4 max-w-md">
                <FormField
                  control={form.control}
                  name="repoUrl"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>GitHub Repo URL</FormLabel>
                      <FormControl>
                        <Input type="password" placeholder="https://github.com/user/repo" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="token"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Personal Access Token</FormLabel>
                      <FormControl>
                        <Input type="password" placeholder="ghp_..." {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="branch"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Branch</FormLabel>
                      <FormControl>
                        <Input type="text" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <Button type="submit" disabled={connectMutation.isPending}>
                  {connectMutation.isPending ? "Connecting..." : "Connect Repository"}
                </Button>
              </form>
            </Form>
          </CardContent>
        </Card>
      )}

      <Dialog open={pushDialogOpen} onOpenChange={setPushDialogOpen}>
        <DialogContent className="bg-card border-border">
          <DialogHeader>
            <DialogTitle>Push Changes</DialogTitle>
          </DialogHeader>
          <div className="py-4">
            <Input 
              placeholder="Commit message..." 
              value={commitMessage}
              onChange={e => setCommitMessage(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setPushDialogOpen(false)}>Cancel</Button>
            <Button onClick={onPush} disabled={!commitMessage || pushMutation.isPending}>Push to GitHub</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
