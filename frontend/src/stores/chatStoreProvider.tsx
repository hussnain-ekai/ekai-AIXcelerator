'use client';

import { createContext, useContext, useRef } from 'react';
import type { ReactNode } from 'react';
import { useStore } from 'zustand';
import { createChatStore } from './chatStore';
import type { ChatStore } from './chatStore';
import type { ChatState } from './chatStore';

const ChatStoreContext = createContext<ChatStore | null>(null);

/**
 * Provides a scoped ChatStore instance. Each mount creates its own store,
 * so navigating between data products gives each one isolated state.
 */
export function ChatStoreProvider({ children }: { children: ReactNode }): ReactNode {
  const storeRef = useRef<ChatStore | null>(null);
  if (storeRef.current === null) {
    storeRef.current = createChatStore();
  }
  return (
    <ChatStoreContext.Provider value={storeRef.current}>
      {children}
    </ChatStoreContext.Provider>
  );
}

/**
 * Read from the scoped ChatStore via a selector.
 * Same API as the old global `useChatStore((s) => s.foo)`.
 */
export function useChatStore<T>(selector: (state: ChatState) => T): T {
  const store = useContext(ChatStoreContext);
  if (!store) {
    throw new Error('useChatStore must be used within a ChatStoreProvider');
  }
  return useStore(store, selector);
}

/**
 * Get the raw store API for imperative access (getState/setState).
 * Replaces all `useChatStore.getState()` calls.
 */
export function useChatStoreApi(): ChatStore {
  const store = useContext(ChatStoreContext);
  if (!store) {
    throw new Error('useChatStoreApi must be used within a ChatStoreProvider');
  }
  return store;
}
