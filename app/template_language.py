"""Bounded, typed mail-template interpreter. No Python evaluation or attributes."""
import json
import operator
import re
from dataclasses import dataclass

MAX_OUTPUT = 200000
MAX_ITERATIONS = 10000
MAX_DEPTH = 3
MISSING = object()
IDENT = r'[A-Za-z][A-Za-z0-9_]*'
PATH = IDENT + r'(?:\.' + IDENT + r')*'
LEX = re.compile(r'\s+|(?:"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')|-?\d+(?:\.\d+)?|==|!=|>=|<=|[><()[\],]|' + PATH)
COMPARE = {'==': operator.eq, '!=': operator.ne, '>': operator.gt,
           '<': operator.lt, '>=': operator.ge, '<=': operator.le}
RESERVED = {'for', 'in', 'with', 'endfor', 'and', 'or', 'true', 'false'}


class TemplateSyntaxError(ValueError):
    def __init__(self, message, source='', offset=0):
        self.message = message
        self.line = source.count('\n', 0, offset) + 1
        self.column = offset - source.rfind('\n', 0, offset)
        super().__init__(f'第 {self.line} 行，第 {self.column} 列：{message}')

    def detail(self):
        return dict(message=self.message, line=self.line, column=self.column)


def nest(flat):
    result = {}
    for path, value in flat.items():
        node = result
        parts = path.split('.')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return result


def environment(catalog):
    # Build from the flat public values on each call so stale-result invalidation
    # remains authoritative. Explicit and dynamic references share these objects.
    values = nest(catalog['values'])
    schema = catalog.get('schema') or nest({r['name']: r['type'] for r in catalog['rows']})
    if 'listener_names' in catalog:
        values['global']['alllistener'] = [values[name] for name in catalog['listener_names']]
    return values, schema


def scalar(kind):
    return isinstance(kind, str)


def numeric(kind):
    return kind in ('integer', 'number') if scalar(kind) else False


@dataclass
class Expression:
    op: str
    value: object
    args: list
    kind: object

    def evaluate(self, scope):
        if self.op == 'literal':
            return self.value
        if self.op == 'path':
            value = scope
            for part in self.value.split('.'):
                if not isinstance(value, dict) or part not in value:
                    return MISSING
                value = value[part]
            return MISSING if value is None else value
        args = [arg.evaluate(scope) for arg in self.args]
        if self.op == 'list':
            if any(x is MISSING for x in args):
                raise ValueError('列表引用当前值未知')
            return args
        # Missing optional data makes the WHOLE predicate unavailable, even !=
        # or a true branch of an OR. Static checking has already caught typos.
        if any(x is MISSING for x in args):
            return MISSING
        if self.op == 'and':
            return all(args)
        if self.op == 'or':
            return any(args)
        return COMPARE[self.op](*args)


class ExpressionParser:
    def __init__(self, text, schema, fail, references):
        self.schema, self.fail, self.references = schema, fail, references
        self.tokens = []
        pos = 0
        while pos < len(text):
            match = LEX.match(text, pos)
            if not match:
                fail('不支持的表达式')
            if not match[0].isspace():
                self.tokens.append(match[0])
            pos = match.end()
        if len(self.tokens) > 256:
            fail('表达式过长')
        self.index = 0
        self.depth = 0

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self):
        token = self.peek()
        self.index += 1
        return token

    def expect(self, token):
        if self.take() != token:
            self.fail('缺少 ' + token)

    def parse(self):
        result = self.boolean('or')
        if self.peek() is not None:
            self.fail('表达式格式错误')
        return result

    def boolean(self, op):
        parse = (lambda: self.boolean('and')) if op == 'or' else self.comparison
        left = parse()
        while self.peek() == op:
            self.take()
            right = parse()
            if left.kind != 'boolean' or right.kind != 'boolean':
                self.fail('逻辑运算需要布尔值')
            left = Expression(op, None, [left, right], 'boolean')
        return left

    def comparison(self):
        left = self.atom()
        if self.peek() in COMPARE:
            op = self.take()
            right = self.atom()
            compatible = left.kind == right.kind or (numeric(left.kind) and numeric(right.kind))
            if not scalar(left.kind) or not compatible:
                self.fail('比较值类型不一致')
            if op not in ('==', '!=') and not numeric(left.kind):
                self.fail('大小比较需要数字')
            left = Expression(op, None, [left, right], 'boolean')
        return left

    def atom(self):
        self.depth += 1
        if self.depth > 24:
            self.fail('表达式嵌套过深')
        try:
            token = self.take()
            if token is None:
                self.fail('表达式不完整')
            if token == '(':
                result = self.boolean('or')
                self.expect(')')
                return result
            if token == '[':
                args = []
                if self.peek() != ']':
                    while True:
                        value = self.atom()
                        if value.op != 'path':
                            self.fail('显式列表只能包含变量引用')
                        args.append(value)
                        if self.peek() != ',':
                            break
                        self.take()
                self.expect(']')
                if not args:
                    self.fail('显式列表不能为空')
                if any(x.kind != args[0].kind for x in args):
                    self.fail('列表元素类型不一致')
                return Expression('list', None, args, [args[0].kind])
            if token in ('true', 'false'):
                return Expression('literal', token == 'true', [], 'boolean')
            if token.startswith(('"', "'")):
                # JSON string escapes; single quotes are accepted without eval.
                raw = token[1:-1]
                try:
                    if token[0] == "'":
                        converted, index = [], 0
                        while index < len(raw):
                            char = raw[index]
                            if char == '\\' and index + 1 < len(raw):
                                following = raw[index + 1]
                                converted.append("'" if following == "'" else char + following)
                                index += 2
                            else:
                                converted.append('\\"' if char == '"' else char)
                                index += 1
                        value = json.loads('"' + ''.join(converted) + '"')
                    else:
                        value = json.loads(token)
                except ValueError:
                    self.fail('字符串格式错误')
                return Expression('literal', value, [], 'string')
            if re.fullmatch(r'-?\d+(?:\.\d+)?', token):
                return Expression('literal', float(token) if '.' in token else int(token), [], 'number' if '.' in token else 'integer')
            if not re.fullmatch(PATH, token) or token in RESERVED:
                self.fail('变量格式错误')
            kind = self.schema
            for part in token.split('.'):
                if not isinstance(kind, dict) or part not in kind:
                    self.fail('不存在的变量或字段：' + token)
                kind = kind[part]
            self.references.add(token)
            return Expression('path', token, [], kind)
        finally:
            self.depth -= 1


class Program:
    def __init__(self, source, catalog):
        self.source = source
        self.references = set()
        self.tokens = []
        self.values, schema = environment(catalog)
        self.nodes = []
        stack = [(self.nodes, schema)]
        pos = 0
        count = 0
        while pos < len(source):
            match = re.search(r'{{|{%|}}|%}', source[pos:])
            if not match:
                stack[-1][0].append(('text', source[pos:]))
                break
            start = pos + match.start()
            if start > pos:
                stack[-1][0].append(('text', source[pos:start]))
            opener = match[0]
            def fail(message, offset=start):
                raise TemplateSyntaxError(message, source, offset)
            if opener not in ('{{', '{%'):
                fail('多余的结束标签')
            close = '}}' if opener == '{{' else '%}'
            end = source.find(close, start + 2)
            if end < 0:
                fail('标签未闭合')
            text = source[start + 2:end].strip()
            nodes, scope = stack[-1]
            count += 1
            if count > 4000:
                fail('模板标签过多')
            if opener == '{{':
                if not re.fullmatch(PATH, text):
                    fail('变量格式错误')
                expr = ExpressionParser(text, scope, fail, self.references).parse()
                if expr.op != 'path':
                    fail('需要变量引用')
                if not scalar(expr.kind):
                    fail('列表或对象不能直接输出')
                nodes.append(('value', expr))
                self.tokens.append(dict(start=start, end=end + 2))
            elif text == 'endfor':
                if len(stack) == 1:
                    fail('多余的 endfor')
                stack.pop()
            else:
                loop = re.fullmatch(r'for\s+(' + IDENT + r')\s+in\s+(.+)', text, re.S)
                if not loop:
                    fail('循环语句格式错误')
                if len(stack) > MAX_DEPTH:
                    fail('循环最多嵌套 3 层')
                name, expression = loop.groups()
                if name in scope or name in RESERVED:
                    fail('循环变量名冲突：' + name)
                # Split at a lexical keyword, never inside strings or paths.
                parser = ExpressionParser(expression, scope, fail, self.references)
                target = parser.atom()
                if target.op not in ('path', 'list') or not isinstance(target.kind, list):
                    fail('循环目标必须为列表')
                local = scope | {name: target.kind[0]}
                condition = None
                if parser.peek() == 'with':
                    parser.take()
                    parser.schema = local
                    condition = parser.boolean('or')
                    if condition.kind != 'boolean':
                        fail('筛选条件必须为布尔值')
                if parser.peek() is not None:
                    fail('循环语句格式错误')
                children = []
                nodes.append(('loop', name, target, condition, children))
                stack.append((children, local))
            pos = end + 2
        if len(stack) != 1:
            raise TemplateSyntaxError('缺少 endfor', source, len(source))

    def render(self):
        output = []
        size = iterations = 0
        def append(value):
            nonlocal size
            size += len(value)
            if size > MAX_OUTPUT:
                raise ValueError('邮件内容超过 20 万字')
            output.append(value)
        def visit(nodes, scope):
            nonlocal iterations
            for node in nodes:
                if node[0] == 'text':
                    append(node[1])
                elif node[0] == 'value':
                    value = node[1].evaluate(scope)
                    if value is MISSING:
                        raise ValueError('变量当前值未知：' + node[1].value)
                    append(str(value).lower() if isinstance(value, bool) else str(value))
                else:
                    _, name, target, condition, children = node
                    values = target.evaluate(scope)
                    if values is MISSING:
                        raise ValueError('列表当前值未知')
                    if not isinstance(values, list):
                        raise ValueError('循环目标当前值不是列表')
                    for value in values:
                        iterations += 1
                        if iterations > MAX_ITERATIONS:
                            raise ValueError('循环展开次数过多')
                        local = scope | {name: value}
                        result = condition.evaluate(local) if condition else True
                        if result is not MISSING and result is True:
                            visit(children, local)
        visit(self.nodes, self.values)
        return ''.join(output)
