<script setup>
defineProps({ name: String, size: { type: [Number, String], default: 20 } })
const paths = {
 mail: ['M4 5h16v14H4z','m4 6 8 6 8-6'], inbox: ['M4 4h16v16H4z','M4 13h4l2 3h4l2-3h4'],
 grid: ['M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z'],
 phone: ['M8 3h8v18H8z','M11 18h2'], search: ['M20 20l-5-5','M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0'],
 refresh: ['M20 8a8 8 0 0 0-14-3L3 8','M3 3v5h5','M4 16a8 8 0 0 0 14 3l3-3','M21 21v-5h-5'],
 shield: ['M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z','m8 12 3 3 5-6'],
 check: ['m5 12 4 4L19 6'], close: ['m6 6 12 12M18 6 6 18'], chevron: ['m9 5 7 7-7 7'],
 archive: ['M4 8h16v13H4z','M3 3h18v5H3z','M10 12h4'], logout: ['M9 4H4v16h5','M9 12h12m-4-4 4 4-4 4'],
 clock: ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0','M12 7v5l3 2'],
 settings: ['M4 7h16M4 17h16','M8 4v6M16 14v6'], user: ['M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0','M4 21v-2a8 8 0 0 1 16 0v2'],
 lock: ['M6 10h12v11H6z','M8 10V7a4 4 0 0 1 8 0v3'], eye: ['M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12','M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0'],
 copy: ['M9 8h12v13H9z','M5 16H3V3h13v2'], menu: ['M4 6h16M4 12h16M4 18h16'],
 info: ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0','M12 11v6M12 7h.01'],
 arrow: ['M4 12h16m-6-6 6 6-6 6'], file: ['M6 3h8l4 4v14H6z','M14 3v5h4M9 12h6M9 16h6'],
 bell: ['M5 17h14l-2-4V8a5 5 0 0 0-10 0v5z','M10 21h4'],
 spark: ['m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z']
}
</script>
<template><svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path v-for="(d,i) in (paths[name] || paths.mail)" :key="i" :d="d" /></svg></template>
