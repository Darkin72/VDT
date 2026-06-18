<script lang="ts">
  type Message = {
    role: 'user' | 'assistant'
    content: string
  }

  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8090'

  let input = $state('')
  let isSending = $state(false)
  let error = $state('')
  let messages = $state<Message[]>([
    {
      role: 'assistant',
      content: 'Chào bạn, mình sẵn sàng trả lời qua backend streaming.',
    },
  ])

  async function sendMessage() {
    const text = input.trim()
    if (!text || isSending) return

    messages = [...messages, { role: 'user', content: text }, { role: 'assistant', content: '' }]
    input = ''
    error = ''
    isSending = true

    try {
      const response = await fetch(`${apiBaseUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      })

      if (!response.ok || !response.body) {
        throw new Error(`Backend returned ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()

      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        const chunk = decoder.decode(value, { stream: true })
        const lastIndex = messages.length - 1
        messages[lastIndex] = {
          ...messages[lastIndex],
          content: messages[lastIndex].content + chunk,
        }
        messages = [...messages]
      }
    } catch (err) {
      error = err instanceof Error ? err.message : 'Không gọi được backend'
      const lastIndex = messages.length - 1
      messages[lastIndex] = {
        role: 'assistant',
        content: 'Xin lỗi, hiện tại mình chưa kết nối được backend.',
      }
      messages = [...messages]
    } finally {
      isSending = false
    }
  }
</script>

<main class="shell">
  <section class="chat-panel" aria-label="Chatbot">
    <header class="topbar">
      <div>
        <p class="eyebrow">VDT chatbot</p>
        <h1>Simple LangChain Chat</h1>
      </div>
      <a class="graph-link" href="http://157.10.53.238:21150" target="_blank" rel="noreferrer">GraphDB</a>
    </header>

    <div class="messages" aria-live="polite">
      {#each messages as message}
        <article class:assistant={message.role === 'assistant'} class:user={message.role === 'user'}>
          <span>{message.role === 'assistant' ? 'AI' : 'You'}</span>
          <p>{message.content || 'Đang trả lời...'}</p>
        </article>
      {/each}
    </div>

    {#if error}
      <p class="error">{error}</p>
    {/if}

    <form class="composer" onsubmit={(event) => { event.preventDefault(); sendMessage() }}>
      <input bind:value={input} placeholder="Nhập câu hỏi..." aria-label="Tin nhắn" disabled={isSending} />
      <button type="submit" disabled={isSending || !input.trim()}>
        {isSending ? 'Đang gửi' : 'Gửi'}
      </button>
    </form>
  </section>
</main>
