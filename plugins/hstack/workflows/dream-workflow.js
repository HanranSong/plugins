export const meta = {
  name: 'dream-workflow',
  description: 'The work of /hstack:dream: one analyzer per transcript in groups of about 20, then the organizer once',
  whenToUse: 'Only /hstack:dream starts it, with the args its prepare step prints. Do not run it on its own.',
  phases: [
    { title: 'Analyze', detail: 'one transcript-analyzer per transcript, each group finishing before the next starts' },
    { title: 'Organize', detail: 'memory-organizer, once, after every analyzer' },
  ],
}

// This script stands in for the cookbook's lead agent. The lead wrote a workflow from the work request
// ("Work through the transcripts in groups of about 20, finishing each group before you start the next",
// then the organizer once), recorded a failed agent without stopping the run, and replied with a one-line
// JSON report. This does the same, every run alike, and returns that report's fields.

const ANALYZER = 'hstack:transcript-analyzer'
const ORGANIZER = 'hstack:memory-organizer'

// What each agent returns, as its prompt asks: the analyzer {summary}, the organizer {index_entries, changes}.
// Each also says whether it did all its work: a summary or a list of changes alone would count an agent whose
// reads or writes were refused as done, and the run would move past sessions whose facts never reached memory,
// or delete placeholders whose index entries were never repointed.
const SUMMARY = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    analyzed: {
      type: 'boolean',
      description:
        'false if any of your work is still undone as you return: a part of the transcript you could not ' +
        'read, or a change you could not make (say which in the summary); true otherwise',
    },
  },
  required: ['summary', 'analyzed'],
}
const ORGANIZED = {
  type: 'object',
  properties: {
    index_entries: { type: 'integer' },
    changes: { type: 'array', items: { type: 'string' } },
    complete: {
      type: 'boolean',
      description:
        'false if any change is still left undone as you return (list it under changes); true otherwise',
    },
  },
  required: ['index_entries', 'changes', 'complete'],
}

// The task each analyzer gets: the one the cookbook shows its lead agent wrote, with this run's paths.
function analyzerTask(transcript, memoryDir, preferences) {
  return (
    `Please read the transcript at ${transcript} and add the durable, verified facts it shows to the ` +
    `long-term memory directory at ${memoryDir}. Other analyzers are working on other transcripts into the ` +
    `same directory at the same time, so check what is already there before writing and add to or update ` +
    `existing files rather than overwrite them. Write only inside that directory, and do not modify the ` +
    `transcript. Take facts only from the transcript; the git status Claude Code gave you describes the ` +
    `project now, not the session. When done, submit a short summary of what you added.` +
    preferences
  )
}

function organizerTask(memoryDir, preferences) {
  return `Every analyzer has finished. Tidy the whole memory directory at ${memoryDir}.` + preferences
}

// The cookbook's analyzer_preferences and organizer_preferences: the developer's instructions for this run,
// or nothing. The cookbook put them in each agent's system prompt; here they end the task message.
function analyzerPreferences(text) {
  if (!text) return ''
  return (
    '\n\nPreferences for this run, from the developer; apply them within the methodology below, ' +
    'where they refine what to emphasize or how to write but do not override your role, the ' +
    'transcript format, or the concurrency rules above:\n' +
    `<user_instructions>\n${text}\n</user_instructions>`
  )
}

function organizerPreferences(text) {
  if (!text) return ''
  return (
    "\n\nThe developer's preferences about structure or emphasis for this run; apply them within " +
    `the rules above:\n<user_instructions>\n${text}\n</user_instructions>`
  )
}

function fileName(path) {
  return String(path).split('/').pop()
}

if (!args || typeof args !== 'object' || !Array.isArray(args.transcripts) || typeof args.memory_dir !== 'string') {
  return { error: 'dream-workflow takes the args /hstack:dream prepares. Run /hstack:dream instead.' }
}

const memoryDir = args.memory_dir
const inputs = args.transcripts
const groupSize = Number.isInteger(args.group_size) && args.group_size > 0 ? args.group_size : 20
const instructions = typeof args.instructions === 'string' ? args.instructions.trim() : ''

const outcomes = []
phase('Analyze')
for (let start = 0; start < inputs.length; start += groupSize) {
  const group = inputs.slice(start, start + groupSize)
  log(`Analyzing transcripts ${start + 1} to ${start + group.length} of ${inputs.length}`)
  const results = await parallel(
    group.map((transcript) => async () => {
      try {
        const result = await agent(analyzerTask(transcript, memoryDir, analyzerPreferences(instructions)), {
          label: `analyze ${fileName(transcript)}`,
          phase: 'Analyze',
          agentType: ANALYZER,
          schema: SUMMARY,
        })
        if (!result) return { input: transcript, reason: 'the analyzer returned no result: it failed or was skipped' }
        if (result.analyzed !== true) {
          return { input: transcript, reason: `the analyzer could not finish: ${result.summary}` }
        }
        return { input: transcript, summary: result.summary }
      } catch (error) {
        return { input: transcript, reason: `the analyzer failed: ${String(error)}` }
      }
    }),
  )
  results.forEach((outcome, i) => {
    outcomes.push(outcome || { input: group[i], reason: 'the analyzer returned no result' })
  })
}

phase('Organize')
let organized = false
let indexEntries = null
let changes = []
let organizerError = null
try {
  const result = await agent(organizerTask(memoryDir, organizerPreferences(instructions)), {
    label: 'organize memory',
    phase: 'Organize',
    agentType: ORGANIZER,
    schema: ORGANIZED,
  })
  if (result) {
    indexEntries = result.index_entries
    changes = result.changes
    if (result.complete === true) organized = true
    else organizerError = 'the organizer could not make every change it needed (see its changes)'
  } else {
    organizerError = 'the organizer returned no result: it failed or was skipped'
  }
} catch (error) {
  organizerError = `the organizer failed: ${String(error)}`
}

const failures = outcomes.filter((o) => o.reason).map((o) => ({ input: o.input, reason: o.reason }))
const report = {
  inputs_expected: inputs.length,
  inputs_processed: inputs.length - failures.length,
  failures,
  organized,
  index_entries: indexEntries,
  changes,
}
if (organizerError) report.organizer_error = organizerError
return {
  report,
  summaries: outcomes.filter((o) => o.summary).map((o) => ({ input: fileName(o.input), summary: o.summary })),
}
