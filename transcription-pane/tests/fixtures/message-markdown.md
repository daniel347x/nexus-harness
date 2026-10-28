# Manager reply

**Ready** and *readable*, with `inline code`.

- First item
- Second item
  - Nested item

1. Ordered first
2. Ordered second

| Area | Result |
| :--- | ---: |
| Markdown | **Ready** |
| Unicode | 🌈 café |

```javascript
const literal = '<img src=x onerror="window.bad=1">';
console.log(literal);
```

> A quoted decision.

[Safe link](https://example.com/path?q=1 "Example")
[Unsafe link](javascript:alert(1))

<img src=x onerror="window.bad=1">
<script>window.bad=1</script>
<svg onload="window.bad=1"><a href="javascript:alert(1)">bad</a></svg>

LAST-MARKDOWN-TAIL
