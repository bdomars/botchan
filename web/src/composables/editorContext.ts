import { inject, type InjectionKey } from "vue";
import type { GuildEditor } from "./useGuildEditor";

export const EditorKey: InjectionKey<GuildEditor> = Symbol("guild-editor");

// The editor App.vue provides to every component below it.
export function useEditor(): GuildEditor {
    const editor = inject(EditorKey);
    if (!editor) throw new Error("useEditor() needs App.vue's provide(EditorKey, …).");
    return editor;
}
