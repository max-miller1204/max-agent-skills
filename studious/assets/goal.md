---
type: goal
id: Exam2
course: CS4804
title: CS4804 Exam 2
mode: deadline
date: 2026-11-20
calendar_event_id: abc123
status: active
cram: false
---
# CS4804 Exam 2

One or two lines: format, scope, where the review guide is.

## Lessons

```base
filters:
  and:
    - file.inFolder("courses")
    - file.path.contains("/lessons/")
    - course == this.course
    - goals.contains(this.id)
properties:
  note.title:
    displayName: Title
  note.minutes:
    displayName: Minutes
views:
  - type: table
    name: Lessons
    order:
      - file.name
      - title
      - minutes
      - verified
      - done
    sort:
      - property: file.name
        direction: ASC
    summaries:
      minutes: Sum
```
