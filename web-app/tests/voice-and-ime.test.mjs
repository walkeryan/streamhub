import { test } from 'node:test'
import assert from 'node:assert/strict'
import { offersVoice, parseShellMessage, voiceCommand } from '../src/shellProtocol.ts'

test('the TV reports voice search as it listens, hears and finishes; words only where there are some', () => {
  assert.deepEqual(parseShellMessage({ v: 1, kind: 'voice', state: 'listening', text: null, error: null }), { kind: 'voice', state: 'listening', text: null, error: null })
  assert.deepEqual(parseShellMessage('{"v":1,"kind":"voice","state":"final","text":" the long\\u0007ebb ","error":null}'), { kind: 'voice', state: 'final', text: 'the long ebb', error: null })
  assert.equal(parseShellMessage({ v: 1, kind: 'voice', state: 'final', text: '' }), null)
  assert.deepEqual(parseShellMessage({ v: 1, kind: 'voice', state: 'error', text: null, error: 'weird' }), { kind: 'voice', state: 'error', text: null, error: 'other' })
  assert.equal(parseShellMessage({ v: 1, kind: 'voice', state: 'shouting' }), null)
})

test('the keyboard closing is a message of its own', () => {
  assert.deepEqual(parseShellMessage({ v: 1, kind: 'ime', visible: false }), { kind: 'ime', visible: false })
  assert.equal(parseShellMessage({ v: 1, kind: 'ime', visible: 'no' }), null)
})

test('voice is offered only when the TV says so, and the page asks it to start and stop', () => {
  assert.equal(offersVoice('{"voice_search":true}'), true)
  for (const c of ['{"voice_search":false}', '{}', 'not json', undefined]) assert.equal(offersVoice(c), false)
  assert.deepEqual(JSON.parse(voiceCommand('start')), { v: 1, kind: 'voice_command', command: 'start' })
})
