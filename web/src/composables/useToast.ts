import { onBeforeUnmount, ref } from "vue";

const TOAST_MS = 2600;

export function useToast() {
    const toast = ref("");
    let timer: number | undefined;

    function notify(message: string) {
        toast.value = message;
        window.clearTimeout(timer);
        timer = window.setTimeout(() => {
            toast.value = "";
        }, TOAST_MS);
    }

    onBeforeUnmount(() => window.clearTimeout(timer));

    return { toast, notify };
}
