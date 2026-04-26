import ast
code = "open('file.txt', f'w')"
tree = ast.parse(code)
call = tree.body[0].value
print(type(call.args[1]))
