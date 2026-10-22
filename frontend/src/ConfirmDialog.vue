<script setup>
import {ref,watch,nextTick} from 'vue'
import {confirmation,resolveConfirmation} from './ui'
const dialog=ref(null)
watch(confirmation,async value=>{if(value){await nextTick();dialog.value.showModal()}else dialog.value?.close()})
</script>
<template><dialog ref="dialog" class="unified-confirm" @cancel.prevent="resolveConfirmation(false)"><h3>确认操作</h3><p>{{confirmation}}</p><footer><button class="button" @click="resolveConfirmation(false)">取消</button><button class="button primary" @click="resolveConfirmation(true)">确认</button></footer></dialog></template>
