import React, { useState } from "react";
import { format } from "date-fns";
import { FileCode2, Folder, Clock, HardDrive } from "lucide-react";
import { 
  useGetBotFiles, 
  useGetBotFileContent,
  getGetBotFileContentQueryKey
} from "@workspace/api-client-react";
import { Skeleton } from "@/components/ui/skeleton";

function formatBytes(bytes: number, decimals = 2) {
  if (!+bytes) return '0 Bytes'
  const k = 1024
  const dm = decimals < 0 ? 0 : decimals
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`
}

export default function Files() {
  const { data: filesData, isLoading: isFilesLoading } = useGetBotFiles();
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  const { data: fileContent, isFetching: isContentFetching } = useGetBotFileContent(
    { path: selectedFile || "" },
    { query: { enabled: !!selectedFile, queryKey: getGetBotFileContentQueryKey({ path: selectedFile || "" }) } }
  );

  return (
    <div className="h-full flex flex-col p-8">
      <div className="mb-6 shrink-0">
        <h1 className="text-3xl font-bold tracking-tight text-foreground">File Browser</h1>
        <p className="text-muted-foreground mt-1">Explore and inspect local bot source files.</p>
      </div>

      <div className="flex-1 min-h-0 flex gap-6 rounded-xl border border-border bg-card overflow-hidden shadow-sm">
        {/* Left: File List */}
        <div className="w-1/3 min-w-[300px] max-w-sm border-r border-border flex flex-col bg-card/50">
          <div className="p-3 border-b border-border bg-muted/20 font-semibold text-sm flex items-center gap-2 text-foreground">
            <Folder className="w-4 h-4 text-primary" />
            Project Root
          </div>
          <div className="flex-1 overflow-auto p-2 space-y-1">
            {isFilesLoading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full bg-secondary rounded-md" />
              ))
            ) : filesData?.files.length === 0 ? (
              <div className="p-4 text-center text-sm text-muted-foreground">No files found.</div>
            ) : (
              filesData?.files.map((file) => (
                <button
                  key={file.path}
                  onClick={() => setSelectedFile(file.path)}
                  className={`w-full text-left px-3 py-2.5 rounded-md flex flex-col gap-1.5 transition-colors ${
                    selectedFile === file.path 
                      ? "bg-primary/10 border border-primary/20 text-primary-foreground" 
                      : "hover:bg-secondary border border-transparent text-foreground"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <FileCode2 className={`w-4 h-4 ${selectedFile === file.path ? "text-primary" : "text-muted-foreground"}`} />
                    <span className="font-mono text-sm truncate">{file.name}</span>
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-muted-foreground px-6">
                    <span className="flex items-center gap-1"><HardDrive className="w-3 h-3" /> {formatBytes(file.size)}</span>
                    <span className="flex items-center gap-1"><Clock className="w-3 h-3" /> {format(new Date(file.lastModified), "MMM d, HH:mm")}</span>
                  </div>
                </button>
              ))
            )}
          </div>
        </div>

        {/* Right: File Content */}
        <div className="flex-1 flex flex-col min-w-0 bg-[#0F111A]">
          {selectedFile ? (
            <>
              <div className="px-4 py-2 border-b border-border/50 bg-[#141824] flex items-center gap-2 shrink-0">
                <FileCode2 className="w-4 h-4 text-muted-foreground" />
                <span className="text-sm font-mono text-muted-foreground">{selectedFile}</span>
              </div>
              <div className="flex-1 overflow-auto p-4 text-sm font-mono text-[#A6ACCD]">
                {isContentFetching ? (
                  <div className="animate-pulse space-y-2">
                    <div className="h-4 bg-muted/20 w-1/2 rounded" />
                    <div className="h-4 bg-muted/20 w-3/4 rounded" />
                    <div className="h-4 bg-muted/20 w-2/3 rounded" />
                  </div>
                ) : (
                  <pre className="whitespace-pre-wrap break-words">
                    <code>{fileContent?.content || "File is empty."}</code>
                  </pre>
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-muted-foreground flex-col gap-3">
              <FileCode2 className="w-12 h-12 opacity-20" />
              <p>Select a file to view its contents</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
