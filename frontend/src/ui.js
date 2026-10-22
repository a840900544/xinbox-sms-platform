import {ref} from 'vue'
export const confirmation=ref(null)
let finish
export function ask(message){return new Promise(resolve=>{if(finish)finish(false);finish=resolve;confirmation.value=message})}
export function resolveConfirmation(value){confirmation.value=null;const done=finish;finish=null;done?.(value)}
