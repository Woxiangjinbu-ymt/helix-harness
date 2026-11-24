import { createContext, useCallback, useContext, useState } from "react";
import type { ReactNode } from "react";

type WorkspaceContextValue = {
  repo: string;
  setRepo: (id: string) => void;
  rev: number;
  refresh: () => void;
  registerOpen: boolean;
  setRegisterOpen: (open: boolean) => void;
};

const WorkspaceContext = createContext<WorkspaceContextValue>({
  repo: "page-settings",
  setRepo: () => {},
  rev: 0,
  refresh: () => {},
  registerOpen: false,
  setRegisterOpen: () => {},
});

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [repo, setRepo] = useState("page-settings");
  const [rev, setRev] = useState(0);
  const [registerOpen, setRegisterOpen] = useState(false);

  const refresh = useCallback(() => setRev((r) => r + 1), []);

  return (
    <WorkspaceContext.Provider
      value={{
        repo,
        setRepo,
        rev,
        refresh,
        registerOpen,
        setRegisterOpen,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  return useContext(WorkspaceContext);
}
