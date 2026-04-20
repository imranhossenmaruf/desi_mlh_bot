import React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Shield, Database, Github, CheckCircle2, XCircle } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { 
  useGetBotConfig, 
  useSaveBotConfig,
  getGetBotConfigQueryKey 
} from "@workspace/api-client-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const configSchema = z.object({
  telegramToken: z.string().optional(),
  mongoUri: z.string().optional(),
});

type ConfigValues = z.infer<typeof configSchema>;

export default function Config() {
  const queryClient = useQueryClient();
  const { data: config, isLoading } = useGetBotConfig();
  const saveMutation = useSaveBotConfig();

  const form = useForm<ConfigValues>({
    resolver: zodResolver(configSchema),
    defaultValues: {
      telegramToken: "",
      mongoUri: "",
    }
  });

  const onSave = (data: ConfigValues) => {
    // Only send fields that are filled out
    const payload = {
      ...(data.telegramToken ? { telegramToken: data.telegramToken } : {}),
      ...(data.mongoUri ? { mongoUri: data.mongoUri } : {}),
    };
    
    if (Object.keys(payload).length === 0) {
      toast.info("No changes to save");
      return;
    }

    saveMutation.mutate({ data: payload }, {
      onSuccess: () => {
        toast.success("Configuration saved successfully");
        form.reset({ telegramToken: "", mongoUri: "" });
        queryClient.invalidateQueries({ queryKey: getGetBotConfigQueryKey() });
      },
      onError: () => toast.error("Failed to save configuration")
    });
  };

  const StatusIcon = ({ configured }: { configured?: boolean }) => {
    if (configured) return <CheckCircle2 className="w-5 h-5 text-emerald-500" />;
    return <XCircle className="w-5 h-5 text-red-500" />;
  };

  return (
    <div className="p-8 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-foreground">Configuration</h1>
        <p className="text-muted-foreground mt-1">Manage environment variables and credentials.</p>
      </div>

      <div className="grid md:grid-cols-3 gap-6">
        <div className="md:col-span-2">
          <Card className="border-border bg-card">
            <CardHeader>
              <CardTitle>Environment Variables</CardTitle>
              <CardDescription>Update sensitive credentials. Leave blank to keep existing values.</CardDescription>
            </CardHeader>
            <CardContent>
              <Form {...form}>
                <form onSubmit={form.handleSubmit(onSave)} className="space-y-6">
                  <FormField
                    control={form.control}
                    name="telegramToken"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Telegram Bot Token</FormLabel>
                        <FormControl>
                          <Input type="password" placeholder="********************************************" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="mongoUri"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>MongoDB URI</FormLabel>
                        <FormControl>
                          <Input type="password" placeholder="mongodb+srv://..." {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <Button type="submit" disabled={saveMutation.isPending}>
                    {saveMutation.isPending ? "Saving..." : "Save Configuration"}
                  </Button>
                </form>
              </Form>
            </CardContent>
          </Card>
        </div>

        <div>
          <Card className="border-border bg-card h-full">
            <CardHeader>
              <CardTitle>Status</CardTitle>
              <CardDescription>Current credential setup</CardDescription>
            </CardHeader>
            <CardContent>
              {isLoading ? (
                <div className="space-y-4">
                  {[1,2,3].map(i => <div key={i} className="h-10 bg-secondary rounded animate-pulse" />)}
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="flex items-center justify-between p-3 rounded-md bg-secondary/50 border border-border">
                    <div className="flex items-center gap-3">
                      <Shield className="w-4 h-4 text-muted-foreground" />
                      <span className="text-sm font-medium">Telegram</span>
                    </div>
                    <StatusIcon configured={config?.hasTelegramToken} />
                  </div>
                  
                  <div className="flex items-center justify-between p-3 rounded-md bg-secondary/50 border border-border">
                    <div className="flex items-center gap-3">
                      <Database className="w-4 h-4 text-muted-foreground" />
                      <span className="text-sm font-medium">MongoDB</span>
                    </div>
                    <StatusIcon configured={config?.hasMongoUri} />
                  </div>
                  
                  <div className="flex items-center justify-between p-3 rounded-md bg-secondary/50 border border-border">
                    <div className="flex items-center gap-3">
                      <Github className="w-4 h-4 text-muted-foreground" />
                      <span className="text-sm font-medium">GitHub</span>
                    </div>
                    <StatusIcon configured={config?.hasGitHubToken} />
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
