/* ── Globals ──────────────────────────────────────────────────────────────── */
'use strict';

const S = {
  convId:    null,   // currently selected conversation id
  convs:     [],     // [{id, title, updated_at, message_count}]
  streaming: false,
  kbNames:   [],     // 当前对话绑定的知识库名称列表（持久化到 conv JSON）
  _editingConvId: null,  // conv settings modal state
  streamModel: '',       // model name of current stream
  // streaming DOM refs
  streamMsgEl:      null,
  streamContentEl:  null,
  streamReasonEl:   null,
  streamThinkBlock: null,
  toolBlocksEl:     null,   // container for tool-call blocks
  streamBuf:        '',
  reasonBuf:        '',
  // regen / version navigation
  turnVersions:    {},   // { userMsgId: [{row, msgId}, ...] }  — in-session regen history
  _verChoice:      {},   // { userMsgId: idx } — user’s manual version selection
  streamUserMsgId: null, // user_message_id carried by current stream’s start event
  _regenUserMsgId: null, // set by regenMsg(), consumed by sendMessage()
  pendingAttachments: [], // composer attachments before send
  _pendingSend: null,     // queued send payload while cancelling an active stream
};

