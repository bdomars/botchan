import { relativeTime } from "./core";

const numberFormat = new Intl.NumberFormat();

export function number(value: number | null | undefined): string {
    return numberFormat.format(value ?? 0);
}

export function ago(iso: string | null | undefined): string {
    return relativeTime(iso);
}
