import { createContext, useContext } from "react";

/**
 * Page visibility for the keep-alive page pool (App.tsx): a page that has
 * been visited stays mounted and is only hidden with display:none. Pages
 * consume this context to pause polling while hidden and to repair
 * size-sensitive widgets (React Flow) when they become visible again —
 * without ever unmounting business components.
 */
export const PageVisibilityContext = createContext<boolean>(true);

export function usePageVisible(): boolean {
  return useContext(PageVisibilityContext);
}
