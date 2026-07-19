# mypy: ignore-errors
import builtins
from typing import Any#, Optional
import operator as pyop
from pathlib import Path
import struct
import os
import traceback
import time

staticVariables: dict[str, dict] = {}
staticMethods: dict[str, dict] = {}

ENTRY_METHOD_NAME = 'main'
ENTRY = {'entryClass': '', 'entryMethod': ENTRY_METHOD_NAME}

nextHeapId = 0
heap: dict = {}

memory: dict = {'Object': {'name': 'Object', 'methods': {}, 'fields': {}}}
interfaces: dict = {}

class Numeric:
    @classmethod
    def getReprToken(cls):
        return type(cls).__name__.lower()
class Returnable: # Base class in class hierachy for anything that a method could return
    pass

def toSigned(value: int, bits: int) -> int:
    mask = (1 << bits) - 1
    if isinstance(value, (Byte, Short, Int, Long, Double, Float)):
        value = value.get()
    value = int(value)
    value &= mask
    if value >= (1 << (bits - 1)):
        value -= (1 << bits)
    return value

class _IntegerBase(Numeric, Returnable):
    bits: int = 32
    signed: bool = True
    def __init__(self, value: int = 0):
        self.value = toSigned(value, self.bits)
    def __repr__(self):
        return f"{type(self).__name__}({self.value})"
    def get(self):
        return self.value

class Byte(_IntegerBase):
    bits = 8
    @classmethod
    def get_bits(cls):
        return cls.bits
class Short(_IntegerBase):
    bits = 16
    @classmethod
    def get_bits(cls):
        return cls.bits
class Int(_IntegerBase):
    bits = 32
    @classmethod
    def get_bits(cls):
        return cls.bits
class Long(_IntegerBase):
    bits = 64
    @classmethod
    def get_bits(cls):
        return cls.bits

class Double(Numeric, Returnable):
    bits = 64
    def __init__(self, value: float = 0.0):
        if isinstance(value, (Float, Double)):
            value = value.get()
        self.value = float(value)
    @classmethod
    def get_bits(cls):
        return cls.bits
    def get(self): return self.value
    def to_bytes(self): return struct.pack('>d', self.value)
    def __repr__(self): return f"Double({self.value})"
class Float(Numeric, Returnable):
    bits = 32
    @classmethod
    def get_bits(cls):
        return cls.bits
    def __init__(self, value: float = 0.0):
        if isinstance(value, (Float, Double)):
            value = value.get()
        self.value = struct.unpack('>f', struct.pack('>f', float(value)))[0]
    def get(self): return self.value
    def to_bytes(self): return struct.pack('>f', self.value)
    def __repr__(self): return f"Float({self.value})"

class Bool(Returnable):
    def __init__(self, value: bool):
        self.value = value
    def get(self):
        return self.value
    def __repr__(self):
        return f"Bool({self.value})"

class String(Returnable):
    def __init__(self, value: str):
        self.value = value
    def get(self):
        return self.value
    def __repr__(self):
        return f"{self.value}"
class Char(Returnable):
    def __init__(self, value: str):
        self.value = value[0]
    def get(self):
        return self.value
    def __repr__(self):
        return f"Char({self.value})"
class Void(Returnable):
    def __repr__(self):
        return "Void"
class Null():
    def __repr__(self):
        return "Null"
class ExceptionValue(Returnable):
    """Wraps a caught Python exception so DSL code can call e.getMessage() on it,
    while still behaving like a String (via get()) for concatenation/printing."""
    def __init__(self, excType: type, message: str):
        self.excType = excType
        self.message = message
    def get(self):
        return self.message
    def __repr__(self):
        return self.message

class ClassReference(Returnable):
    def __init__(self, className):
        if className not in memory:
            raise Exception(f'Class "{className}" does not exist')
        self.className = className
    def get(self):
        return memory.get(self.className, Exception('This class does not exist'))
    def getClass(self):
        _ = self.get() # Ensures the ClassReference points to something, before dispensing address
        return self.className
class ClassType(Returnable):
    def __init__(self, className: str):
        self.className = className
    def get(self):
        return self.className
    def __repr__(self):
        return f"ClassType({self.className})"

def isAllowedAtThisScope(modifier: str, thisScope: str, isInterface: bool = False) -> bool: 
    isValidModifier(modifier)
    if isInterface:
        return True
    if thisScope == 'this':
        return True
    if modifier == 'public':
        return True
    if modifier == "default": # This will have "isSamePackage" check later
        return True
    if modifier == "protected" and thisScope == "subclass":
        return True
    raise PermissionError('Attempted to access a field or method without sufficient permission')
def isConsistentTypes(thisType: object, otherType: object) -> bool:
    if isinstance(thisType, ClassType) or thisType is ClassType or thisType is ClassReference:
        if isinstance(otherType, ObjectReference) or otherType is ObjectReference:
            if not (otherType.getClass() == thisType.className or thisType.className in getHierarchyOfClass(otherType.getClass())):
                raise Exception(f'Class {otherType.getClass()} does not support class {thisType.className}')
            return True 
    elif isinstance(thisType, ObjectReference) or thisType is ObjectReference:
        if isinstance(otherType, ClassType) or otherType is ClassType:
            if not (thisType.getClass() == otherType.className or otherType.className in getHierarchyOfClass(thisType.getClass())):
                raise Exception(f'Class {otherType.className} does not support class {thisType.getClass()}')
            return True
    if thisType is otherType:
        return True
    if not isinstance(thisType, type):
        thisType = type(thisType)
    if not isinstance(otherType, type):
        otherType = type(otherType)
    try:
        if issubclass(thisType, otherType) or issubclass(otherType, thisType):
            return True
        if issubclass(thisType, _IntegerBase) and issubclass(otherType, _IntegerBase) and thisType.bits == otherType.bits:
            return True
    except TypeError:
        raise Exception(f'Type {otherType.__name__} does not support type {thisType.__name__}')
    
    raise Exception(f'Type {otherType.__name__} does not support type {thisType.__name__}')
def hasCheckdAllExeceptions(ownerName: str, thisMethodName: str, ownerAsInterface: bool = False):
    """
    Static (parse-time) check: for every call inside <thisMethodName>'s body to another
    method that declares exceptions in its own 'throws' clause, verify that this method
    either (a) declares that same exception (or a supertype of it) in its own 'throws'
    clause, or (b) wraps the call in a try/catch that catches that exception (or a
    supertype). Raises SyntaxError if a thrown exception is neither caught nor declared.

    Note: this DSL has no built-in notion of "unchecked" exceptions (there's no clean
    equivalent of Java's RuntimeException umbrella over Python's builtin exception
    classes), so every exception listed in a method's 'throws' clause is treated as
    checked and must be handled by callers.

    This is a best-effort static check, not a full compiler pass: it can only resolve
    calls that are statically determinable from the source text -- unqualified calls
    (own class / inherited), this.method(...), super.method(...),
    ClassName.staticMethod(...), and calls on a method parameter whose declared type is
    a known class. Calls made through local variables, chained calls, or other classes
    defined later in the file (forward references) are not tracked and are silently
    skipped rather than raising a false positive.
    """
    if not ownerAsInterface:
        source = memory
    else:
        source = interfaces
    ownerThrows = source[ownerName]['methods'][thisMethodName].get('throws', [])
    methodInfo = source[ownerName]['methods'][thisMethodName]
    body = methodInfo.get('body')
    if not body:
        return

    bodyParser = Intepreter(body)
    bodyParser.parse()
    tokens = bodyParser.get()

    # --- Pass 1: find try-block ranges and the exception types their catch clauses cover ---
    protectedRanges = []  # list of (start, end, set_of_types)
    i = 0
    while i < len(tokens):
        if tokens[i].get()['type'] == 'TRY':
            j = i + 1
            if j >= len(tokens) or tokens[j].get()['type'] != 'START_DECLARATION':
                i += 1
                continue
            depth = 1
            scan = j + 1
            tryStart = scan
            while scan < len(tokens) and depth > 0:
                t = tokens[scan].get()['type']
                if t == 'START_DECLARATION':
                    depth += 1
                elif t == 'END_DECLARATION':
                    depth -= 1
                scan += 1
            tryEnd = scan  # index right after the matching '}'
            coveredTypes = set()
            while scan < len(tokens) and tokens[scan].get()['type'] == 'CATCH':
                scan += 1
                if scan < len(tokens) and tokens[scan].get()['type'] == 'LPAREN':
                    scan += 1
                if scan < len(tokens) and tokens[scan].get()['type'] == 'IDENTIFIER':
                    excName = tokens[scan].get()['val']
                    excType = getattr(builtins, excName, object)
                    if isinstance(excType, type) and issubclass(excType, (Exception, Warning)):
                        coveredTypes.add(excType)
                    scan += 1
                while scan < len(tokens) and tokens[scan].get()['type'] != 'RPAREN':
                    scan += 1
                scan += 1  # past ')'
                if scan < len(tokens) and tokens[scan].get()['type'] == 'START_DECLARATION':
                    cdepth = 1
                    scan += 1
                    while scan < len(tokens) and cdepth > 0:
                        t2 = tokens[scan].get()['type']
                        if t2 == 'START_DECLARATION':
                            cdepth += 1
                        elif t2 == 'END_DECLARATION':
                            cdepth -= 1
                        scan += 1
            protectedRanges.append((tryStart, tryEnd, coveredTypes))
            i = tryStart
            continue
        i += 1

    def isProtectedAt(pos, excType):
        for (start, end, types) in protectedRanges:
            if start <= pos < end and any(issubclass(excType, t) for t in types):
                return True
        return False

    paramClassMap = {}
    for argName, argType in methodInfo.get('args', {}).items():
        if isinstance(argType, ClassReference):
            paramClassMap[argName] = argType.getClass()

    def resolveCall(idx):
        t0 = tokens[idx].get()
        if t0['type'] == 'IDENTIFIER' and idx + 1 < len(tokens) and tokens[idx + 1].get()['type'] == 'LPAREN':
            return ownerName, t0['val']
        if t0['type'] == 'THIS' and idx + 3 < len(tokens) and tokens[idx + 1].get()['type'] == 'DOT' and tokens[idx + 3].get()['type'] == 'LPAREN':
            return ownerName, tokens[idx + 2].get()['val']
        if t0['type'] == 'SUPER' and idx + 3 < len(tokens) and tokens[idx + 1].get()['type'] == 'DOT' and tokens[idx + 3].get()['type'] == 'LPAREN':
            superRef = memory.get(ownerName, {}).get('super')
            return (superRef.getClass() if superRef else None), tokens[idx + 2].get()['val']
        if t0['type'] == 'IDENTIFIER' and idx + 3 < len(tokens) and tokens[idx + 1].get()['type'] == 'DOT' and tokens[idx + 3].get()['type'] == 'LPAREN':
            qualifier = t0['val']
            methodName = tokens[idx + 2].get()['val']
            if qualifier in memory:
                return qualifier, methodName
            if qualifier in paramClassMap:
                return paramClassMap[qualifier], methodName
        return None, None

    for idx in range(len(tokens)):
        className, methodName = resolveCall(idx)
        if className is None or className not in memory:
            continue
        calleeInfo = memory[className]['methods'].get(methodName)
        if not calleeInfo:
            continue
        for excType in calleeInfo.get('throws', []):
            if isProtectedAt(idx, excType):
                continue
            if any(issubclass(excType, declared) for declared in ownerThrows):
                continue
            raise SyntaxError(
                f"Unreported exception {excType.__name__}; must be caught or declared to be thrown "
                f"in method '{thisMethodName}' of class '{ownerName}' (thrown by '{className}.{methodName}')"
            )
def isValidModifier(modifier: str):
    if not (modifier == 'private' or modifier == 'public' or modifier == 'protected' or modifier == 'default'):
        raise TypeError(f'The modifier provided was not either "private" or "public", was "{modifier}"')
def isValidReturnType(selftype: object):
    if isinstance(selftype, ClassType) or isinstance(selftype, ObjectReference):
        return
    if not issubclass(selftype, Returnable):
        raise Exception('This class is not returnable')
def isChangeable(data: dict):
    if data['final']:
        raise Exception(f'This variable "{data['name']}" is final, and therefore cannot be modified')
def createClass(className: str, classModifier: str, superClass: ClassReference = ClassReference('Object'), implements: list[str] = []):
    isValidModifier(classModifier)
    if className in memory:
        raise NameError(f"Class '{className}' is already defined")
    
    inherited = {}
    inherited_fields = {}
    class_data = memory[superClass.getClass()]
    if superClass.getClass() != "Object":
        for _, m_body in list(class_data['methods'].items()):
            if m_body['modifier'] == 'public':
                inherited[m_body['name']] = m_body

        for _, field in list(class_data['fields'].items()):
            if field['modifier'] == 'public':
                inherited_fields[field['name']] = field

    for ifaceName in implements:
        if not isInterface(ifaceName):
            raise NameError(f'Interface {ifaceName} does not exist')
        for thisIface in [ifaceName] + getHierarchyOfInterface(ifaceName):
            iface_info = interfaces[thisIface]
            for m_name, m_body in iface_info['methods'].items():
                if m_body.get('abstract', True) and m_name not in inherited:
                    inherited[m_name] = {
                        'name': m_body['name'],
                        'modifier': m_body['modifier'],
                        'returns': m_body['returns'],
                        'static': False,
                        'args': m_body['args'],
                        'throws': m_body.get('throws', []),
                        'abstract': True
                    }
                elif not m_body.get('abstract', True) and m_name not in inherited and m_body.get('body'):
                    inherited[m_name] = {
                        'name': m_body['name'],
                        'modifier': m_body['modifier'],
                        'returns': m_body['returns'],
                        'static': m_body.get('static', False),
                        'args': m_body['args'],
                        'throws': m_body.get('throws', []),
                        'body': m_body['body'],
                        'abstract': False
                    }

    memory[className] = {
        'name': className,
        'super': superClass,
        'implements': implements,
        'modifier': classModifier,
        'methods': inherited,
        'fields': inherited_fields
    }
def createMethod(thisClass: ClassReference, methodName: str, methodModifier: str, methodReturnType: object, isStatic: bool = False, methodArgs: dict = {}, throws: list = []): # methodReturnType = Void, Byte, Short, Int, Long, String, ClassReference
    isValidReturnType(methodReturnType)
    isValidModifier(methodModifier)
    classInfo = memory[thisClass.getClass()]
    classInfo['methods'][methodName] = {
        'name': methodName,
        'modifier': methodModifier,
        'returns': methodReturnType,
        'static': isStatic,
        'args': methodArgs,
        'throws': throws
    }
    if isStatic:
        staticMethods[methodName] = {
            'class': thisClass.getClass(),
            'name': methodName,
            'modifier': methodModifier,
            'returns': methodReturnType,
            'args': methodArgs
        }
def argsList(args: dict[str, object]) -> dict: # This sets and compiles an argument list for a method
    """{'name': <returnType>}"""
    for _, expectedType in list(args.items()):
        if isinstance(expectedType, ClassReference):
            expectedType.getClass()
            continue
        if issubclass(type(expectedType), Numeric) or isinstance(expectedType, (Void, String)):
            continue
    return args

def createInterface(interfaceName: str, superInterfaces: list[str] = []):
    if isInterface(interfaceName):
        raise NameError(f'Interface {interfaceName} is already defined')
    interfaces[interfaceName] = {
        'name': interfaceName,
        'super': superInterfaces,
        'methods': {},
    }
def setInterfaceField(interfaceName: str, fieldName: str, fieldType: object, initialValue: object = None):
    if not isInterface(interfaceName):
        raise NameError(f"Interface '{interfaceName}' does not exist")
    
    if initialValue is not None:
        converted_value = convertValue(initialValue, fieldType)
    else:
        converted_value = default_value_for_type(fieldType)

    if interfaceName not in staticVariables:
        staticVariables[interfaceName] = {}
    staticVariables[interfaceName][fieldName] = {
        'class': interfaceName,
        'name': fieldName,
        'modifier': 'public',
        'type': fieldType,
        'value': converted_value,
        'final': True,
        'isInterface': True # Special tag for interface fields
    }
def setInterfaceMethod(interfaceName: str, methodName: str, methodReturnType: object, methodModifier: str, methodArgs: dict = {}, throws: list = [], isAbstract: bool = True, isStatic: bool = False):
    isValidReturnType(methodReturnType)
    isValidModifier(methodModifier)
    interfaceInfo = interfaces.get(interfaceName, {})
    interfaceInfo['methods'][methodName] = {
        'name': methodName,
        'modifier': methodModifier,
        'returns': methodReturnType,
        'args': methodArgs,
        'throws': throws,
        'abstract': isAbstract,
        'static': isStatic
    }

def default_value_for_type(typ: object) -> object:
    if typ is Byte:
        return Byte(0)
    if typ is Short:
        return Short(0)
    if typ is Int:
        return Int(0)
    if typ is Long:
        return Long(0)
    if typ is Float:
        return Float(0.0)
    if typ is Double:
        return Double(0.0)
    if typ is Bool:
        return Bool(False)
    if typ is Char:
        return Char('\0')
    if typ is String:
        return String("")
    return Null()
def setField(className: ClassReference, fieldName: str, fieldModifier: str, fieldType: object, initialValue: object = Null(), isStatic: bool = False, isFinal: bool = False):
    if isinstance(initialValue, Null):
        initialValue = default_value_for_type(fieldType)
    isValidModifier(fieldModifier)
    thisName = className.getClass()
    classInfo = memory[thisName]
    classInfo['fields'][fieldName] = {
        'name': fieldName,
        'modifier': fieldModifier,
        'type': fieldType,
        'value': initialValue,
        'static': isStatic,
        'final': isFinal
    } # getField() is defined inside ObjectReference, as it needs an active, live class to know how the values are working.
    if isStatic:
        if staticVariables.get(thisName, 0) == 0:
            staticVariables[thisName] = {}
        staticVariables[thisName][fieldName] = {
            'class': thisName,
            'name': fieldName,
            'modifier': fieldModifier,
            'type': fieldType,
            'value': initialValue,
            'final': isFinal
        }

class HeapInstance():
    def __init__(self, className: str):
        self.className = className
        self.init: bool = False
        self.fields: dict = {}
        self.locals: dict = {}
    def isInit(self) -> bool:
        return self.init
    def this(self) -> ClassReference:
        return ClassReference(self.className)
    def dump(self) -> dict[str, Any]:
        return {
            'thisClass': self.className,
            'fields': self.fields,
            'locals': self.locals
        }
class ObjectReference():
    def __init__(self, HeapId: int):
        self.HeapId = HeapId
    def get(self) -> HeapInstance:
        if self.HeapId not in heap:
            raise RuntimeError(f"Object ID {self.HeapId} no longer exists")
        return heap[self.HeapId]
    def getClass(self):
        return self.get().className
class StackFrame: # Method call information
    def __init__(self, method_name: str, class_name: str, this_ref: ObjectReference | None = None, args: list = []):
        self.method_name = method_name
        self.class_name = class_name
        self.this = this_ref
        self.locals: dict = {}
        self.args: list = args
        self.returnValue: Returnable = Void() # If return value was void, treated as Null()
    def getArgs(self):
        return self.args
    def setLocal(self, localName: str, localType: object, value: object):
        isConsistentTypes(localType, value)
        value = coerceValue(value, localType)
        self.locals[localName] = {
            'name': localName,
            'type': localType,
            'value': value  
        }
    def changeLocal(self, localName: str, newValue: object,):
        if localName not in self.locals:
            raise RuntimeError(f'The local variable {localName} does not exist')
        localData = self.locals.get(localName, {})
        isConsistentTypes(newValue, localData['type'])
        localData['value'] = coerceValue(newValue, localData['type'])
    def getLocal(self, localName: str, receptorType: object, get: bool = False) -> dict:
        # get parameter means to simply get the variable, if it is set
        # If get = True, receptorType compiles to Null()
        if localName not in self.locals:
            raise RuntimeError(f'The local variable {localName} does not exist')
        if not get:
            isConsistentTypes(receptorType, self.locals.get(localName, {})['type'])
        return self.locals.get(localName, {})
callStack: list[StackFrame] = []

def pushFrame(method_name: str, class_name: str, this_ref: ObjectReference | None = None, args: list = []):
    frame = StackFrame(method_name, class_name, this_ref, args)
    callStack.append(frame)
    return frame
def popFrame() -> StackFrame:
    if not callStack:
        raise RuntimeError("No frame to pop")
    return callStack.pop()
def currentFrame() -> StackFrame:
    if not callStack:
        raise RuntimeError("No active stack frame")
    return callStack[-1]

def newObject(class_name: str) -> ObjectReference:
    global nextHeapId
    
    if class_name not in memory:
        raise NameError(f"Class '{class_name}' is not defined")
    
    obj = HeapInstance(class_name)
    
    class_info = memory[class_name]
    for _, field_def in list(class_info.get('fields', {}).items()):
        field_name = field_def['name']
        default_value = field_def['value']
        obj.fields[field_name] = default_value
    
    obj_id = nextHeapId
    nextHeapId += 1
    heap[obj_id] = obj
    
    return ObjectReference(obj_id)
def newPrimitiveArray(element_type: object, size: int, initial_values: list | None = None) -> 'PrimitiveArray':
    if initial_values is None:
        initial_values = []
    if len(initial_values) < size:
        default = default_value_for_type(element_type)
        while len(initial_values) < size:
            initial_values.append(default)
    return PrimitiveArray(size, initial_values, element_type)

TokenSlice = list['Token']
class PrimitiveArray(Returnable):
    def __init__(self, size: int, initialValues: list, listType: object):
        if size < 0:
            raise ValueError(f'The list size was initialized to an unexpected value, {size}')
        if len(initialValues) < size:
            raise RuntimeError('List was initialized too large')
        if len(initialValues) > size:
            raise RuntimeError('List was initialized too small')
        self.values: list[object] = []
        self.size = size
        for i in initialValues:
            try:
                isConsistentTypes(i, listType)
            except Exception:
                raise Exception(f'List value {i} has inconsistent type {type(i).__name__}, when type {listType.__name__} was expected')
            self.values.append(coerceValue(i, listType))
        self.listType = listType
    def get(self, elementByIndex: int | None = None):
        if elementByIndex is None:
            return self.returnThis()['value']
        if elementByIndex < 0:
            raise Exception('Cannot have negative index')
        if elementByIndex > len(self.values):
            raise Exception('Array index out of bounds, index was too large')
        return self.values[elementByIndex]
    def append(self, value: object):
        try:
            isConsistentTypes(value, self.listType)
        except Exception:
            raise Exception(f'Value {value} has inconsistent type {type(value)}, when type {type(self.listType)} was expected')
        if len(self.values) == self.size:
            raise Exception(f'List has reached maximum size of {self.size}')
        self.values.append(coerceValue(value, self.listType))
    def remove(self, elementByIndex: int):
        if elementByIndex < 0:
            raise Exception('Cannot have negative index')
        if elementByIndex > len(self.values):
            raise Exception('Array index out of bounds, index was too large')
        del self.values[elementByIndex]
    def returnThis(self):
        return {
            'value': self.values,
            'size': self.size,
            'type': self.listType
        }
class PrimitiveArrayWrapper(PrimitiveArray):
    def __init__(self, _type):
        self._type = _type
    def getArrayType(self):
        return self._type
class ArrayAssignment:
    @staticmethod
    def parse(lang: TokenSlice, tokPosition: int, elementTypeTok: str,
              me: StackFrame | None = None, methodArgs: list | None = None):
        # <type>[<int?>] <name> = new <type_consistent>[<int?>]<{args*}?>;
        arrayType = parseTokenAsType(elementTypeTok)
        pos = tokPosition + 1
        if pos >= len(lang) or lang[pos].get()['type'] != 'LBRACKET':
            return None

        close_bracket = matchingBracket(lang, pos)

        declared_size = None
        if close_bracket > pos + 1:
            size_tokens = lang[pos + 1:close_bracket]
            size_value = Expression.evaluate(me, methodArgs, size_tokens)
            if not isinstance(size_value, Int):
                raise RuntimeError("Array size must be an integer")
            declared_size = size_value.get()

        pos = close_bracket + 1
        if pos >= len(lang) or lang[pos].get()['type'] != 'IDENTIFIER':
            raise SyntaxError("Expected identifier after array type")

        var_name = lang[pos].get()['val']
        pos += 1

        if pos >= len(lang):
            raise SyntaxError("Unexpected end of tokens in array declaration")

        if lang[pos].get()['type'] == 'SEMICOLON':
            array_value = newPrimitiveArray(arrayType, declared_size if declared_size is not None else 0)
            return var_name, arrayType, array_value, pos + 1

        if lang[pos].get()['type'] != 'ASSIGN':
            raise SyntaxError("Expected '=' or ';' after array name")
        pos += 1
        if lang[pos+1].get()['type'] not in RETURN_TYPES: # Simple assignment?
            try:
                ClassReference(lang[pos+1].get()['val'])
                isConsistentTypes(parseTokenAsType(lang[pos+1].get()['val']), arrayType)
            except Exception:
                pass
        else:
            isConsistentTypes(parseTokenAsType(lang[pos+1].get()['val']), arrayType)
        rhs_tokens = []
        while pos < len(lang) and lang[pos].get()['type'] != 'SEMICOLON':
            rhs_tokens.append(lang[pos])
            pos += 1
        if pos >= len(lang) or lang[pos].get()['type'] != 'SEMICOLON':
            raise SyntaxError("Expected ';' after array declaration")
        pos += 1
        # rhs: new <type> [] <START_DELC?> ...
        #                         ^ Check here
        rhs_As_Str = [t.get()['val'] for t in rhs_tokens]
        try:
            startInitIdx = rhs_As_Str.index('{')
        except ValueError:
            startInitIdx = -1 # Not?
        if startInitIdx >= 0: # Initialize?
            values = []
            thisElementExpr = []
            for token in rhs_tokens[startInitIdx+1:]:
                if token.get()['val'] == ',':
                    values.append(Expression.evaluate(me, methodArgs, thisElementExpr))
                    thisElementExpr = []
                    isConsistentTypes(type(values[-1]), arrayType)
                    continue
                elif token.get()['val'] == '}':
                    if len(thisElementExpr) > 0: 
                        values.append(Expression.evaluate(me, methodArgs, thisElementExpr)) # Last element
                    break
                elif token.get()['type'] == 'EOF':
                    raise RuntimeError('Reach end of line when more array values were expected')
                thisElementExpr.append(token)
            array_value = PrimitiveArray(len(values), values, arrayType)
        else:
            array_value = Expression.evaluate(me, methodArgs, rhs_tokens)
            if not isinstance(array_value, PrimitiveArray):
                raise RuntimeError(f"Cannot assign non-array value to array variable '{var_name}'")
            if declared_size is not None and array_value.size != declared_size:
                raise RuntimeError(
                    f"Array size mismatch for '{var_name}': declared size {declared_size}, got {array_value.size}"
                )
        return var_name, arrayType, array_value, pos

class EvalTokens():
    TOKENS = {
        "{": "START_DECLARATION",
        "}": "END_DECLARATION",
        "[": "LBRACKET",
        "]": "RBRACKET",
        "(": "LPAREN",
        ")": "RPAREN",
        ":": "COLON",
        ";": "SEMICOLON",
        ",": "COMMA",
        ".": "DOT",
        
        "public": "PUBLIC",
        "private": "PRIVATE",
        "protected": "PROTECTED",
        'default': 'DEFAULT',
        
        "\n": "NEWLINE",
        "final": "FINAL",
        "static": "STATIC",
        "class": "CLASS",
        "interface": "INTERFACE",
        "extends": "EXTENDS",
        "implements": "IMPLEMENTS",
        'abstract': 'ABSTRACT',
        "new": "NEW",
        "return": "RETURN",
        "void": "VOID",
        "null": "NULL",
        "if": "IF",
        "import": "IMPORT_STMT",
        "else": "ELSE",
        "while": "WHILE",
        "for": "FOR",
        "do": "DO",
        "switch": "SWITCH",
        "case": "CASE",
        "break": "BREAK",
        "continue": "CONTINUE",
        "true": "TRUE",
        "false": "FALSE",
        "this": "THIS",
        "super": "SUPER",
        'throw': 'THROW_EXPR', # throw new Exception,
        'throws': 'THROW_STMT', # method A throws Exception
        'try': 'TRY',
        'catch': 'CATCH',
        'finally': 'FINALLY',
        
        "byte": "BYTE_TYPE",
        "short": "SHORT_TYPE",
        "int": "INT_TYPE",
        "long": "LONG_TYPE",
        "String": "STRING_TYPE",
        "char": "CHAR_TYPE",
        "boolean": "BOOLEAN_TYPE",
        "double": "DOUBLE_TYPE",
        "float": "FLOAT_TYPE",

        "println": "NATIVE_PRINT_STMT",
        "print": "NATIVE_PRINT_STMT_nline", 
        
        "=": "ASSIGN",
        "+": "PLUS",
        "-": "MINUS",
        "*": "MULTIPLY",
        "/": "DIVIDE",
        "%": "MODULO",
    }
    SINGLE_CHAR_OP = {
        '!': 'LOGICAL_NOT',
        '>': 'GREATER',
        '<': 'LESS',
        '&': 'BIT_AND',
        '|': 'BIT_OR',
        '^': 'BIT_XOR',
        '~': 'BIT_NOT',
        #UNARY_MINUS
    }
    TWO_CHAR_OP = {
        '++': 'INC',
        '--': 'DEC',
        '&&': 'LOGICAL_AND',
        '||': 'LOGICAL_OR',
        '==': 'EQUALS',
        '!=': 'NOT_EQUALS',
        '>=': 'GREATER_EQUAL',
        '<=': 'LESS_EQUAL',
        '>>': 'RIGHT_SHIFT',
        '<<': 'LEFT_SHIFT',
    }
    PRECEDENCE = {
    'LOGICAL_OR': 1, 'LOGICAL_NOT': 1,
    'LOGICAL_AND': 2,
    'EQUALS': 3, 'NOT_EQUALS': 3,
    'LESS': 4, 'GREATER': 4, 'LESS_EQUAL': 4, 'GREATER_EQUAL': 4,
    'PLUS': 5, 'MINUS': 5,
    'MULTIPLY': 6, 'DIVIDE': 6, 'MODULO': 6, 'INT_DIVIDE': 6
    }
class Token:
    def __init__(self, type_: str, value: str | object, line: int, column: int, truePos: int):
        self.type = type_
        self.value = value
        self.line = line
        self.column = column
        self.truePos = truePos
    def __repr__(self):
        return f"Token({self.type}, '{self.value}', {self.line}:{self.column})"
    def __str__(self):
        return self.__repr__()
    def get(self):
        """Type: The type of the token (id) | Val: The actual contents of this token"""
        return {
            'type': self.type,
            'val': self.value
        }
    def getPos(self):
        return {
            'line': self.line,
            'col': self.column,
            'pos': self.truePos
        }
    @staticmethod
    def wrap(value: object) -> "Token":
        return Token('RESOLVED_VALUE', value, 0, 0, 0)
    @staticmethod
    def splitArgs(tokens: list["Token"]) -> list[list["Token"]]:
        # Splits a token slice
        groups = []
        current: list = []
        depth = 0
        for tok in tokens:
            t = tok.get()['type']
            if t == 'LPAREN': 
                depth += 1
            if t == 'RPAREN': 
                depth -= 1
            if t == 'COMMA' and depth == 0:
                groups.append(current)
                current = []
            else:
                current.append(tok)
        if current: 
            groups.append(current)
        return [g for g in groups if g]
class Intepreter():
    def __init__(self, fileCode: str):
        self.fileCode: str = fileCode
        self.position = 0
        self.line = 1
        self.column = 1
        self.tokens: TokenSlice = []
    def parse(self):
        while self.position < len(self.fileCode):
            thisChar = self.getCharAt()
            if thisChar.isspace():
                if thisChar == "\n":
                    self.add(Token(EvalTokens.TOKENS['\n'], '\n', self.line, self.column, self.position))
                    self.line += 1
                    self.column = 1
                else:
                    self.column += 1
                self.position += 1
                continue
            
            if thisChar == '/' and self.getCharAt(1) == '/':
                self.skipLineComment()
                continue
            if thisChar == '/' and self.getCharAt(1) == '*':
                self.skipBlockComment()
                continue
            if thisChar.isdigit():
                self.parseDigit()
                continue
            
            if thisChar == "'":
                self.parseChar()
                continue
            if thisChar == '"':
                self.parseString()
                continue
            if thisChar.isalpha() or thisChar == '_':
                self.parseIdentifier()
                continue
            
            if self.position + 1 < len(self.fileCode):
                two_char = thisChar + self.fileCode[self.position + 1]
                if two_char in EvalTokens.TWO_CHAR_OP:
                    self.add(Token(EvalTokens.TWO_CHAR_OP[two_char], two_char, self.line, self.column, self.position))
                    self.position += 2
                    self.column += 2
                    continue
            if thisChar in EvalTokens.SINGLE_CHAR_OP:
                self.add(Token(EvalTokens.SINGLE_CHAR_OP[thisChar], thisChar, self.line, self.column, self.position))
                self.position += 1
                self.column += 1
                continue
            if thisChar in EvalTokens.TOKENS:
                self.add(Token(EvalTokens.TOKENS[thisChar], thisChar, self.line, self.column, self.position))
                self.position += 1
                self.column += 1
                continue
            
            raise SyntaxError(f"Unexpected character '{thisChar}' at line {self.line}, column {self.column}, position {self.position}")
        
        self.add(Token('EOF', 'EOF', self.line, self.column, self.position))
    def advance(self, wasNextLine: bool = False):
        self.position += 1
        if not wasNextLine:
            self.column += 1
    def getCharAt(self, offset: int = 0) -> str:
        try:
            return self.fileCode[self.position + offset]
        except IndexError:
            raise EOFError('An unexpected parsing error has occured.')
    def add(self, token: Token):
        self.tokens.append(token)
    def skipLineComment(self):
        while self.position < len(self.fileCode) and self.getCharAt() != '\n':
            self.position += 1
            self.column += 1
        if self.position < len(self.fileCode) and self.getCharAt() == '\n':
            self.position += 1
            self.line += 1
            self.column = 1
    def skipBlockComment(self):
        self.position += 2
        self.column += 2
        while self.position < len(self.fileCode):
            if self.getCharAt() == '*' and self.peek() == '/':
                self.position += 2
                self.column += 2
                return
            if self.getCharAt() == '\n':
                self.line += 1
                self.column = 1
            else:
                self.column += 1
            self.position += 1
        raise SyntaxError(f"Unclosed block comment at line {self.line}")
    def parseDigit(self):
        value = ""
        start_line = self.line
        start_col = self.column
        has_decimal_point = False
        is_float = False
        is_double = False
        is_long = False
        has_exponent = False
        
        while self.position < len(self.fileCode) and self.getCharAt().isdigit():
            value += self.getCharAt()
            self.advance()
        if self.position < len(self.fileCode) and self.getCharAt() == '.':
            has_decimal_point = True
            value += self.getCharAt()
            self.advance()
            if self.position < len(self.fileCode) and self.getCharAt().isdigit():
                while self.position < len(self.fileCode) and self.getCharAt().isdigit():
                    value += self.getCharAt()
                    self.advance()
            else:
                pass
            if self.position < len(self.fileCode) and self.getCharAt().lower() == 'e':
                has_exponent = True
                value += self.getCharAt()
                self.advance()
                
                if self.position < len(self.fileCode) and self.getCharAt() in ['+', '-']:
                    value += self.getCharAt()
                    self.advance()
                if self.position < len(self.fileCode) and self.getCharAt().isdigit():
                    while self.position < len(self.fileCode) and self.getCharAt().isdigit():
                        value += self.getCharAt()
                        self.advance()
                else:
                    raise SyntaxError(f"Expected exponent digits at line {start_line}, column {start_col}")
        suffix = ''
        if self.position < len(self.fileCode):
            suffix = self.getCharAt()
            if suffix in ['L', 'l']:
                if has_decimal_point or has_exponent:
                    raise SyntaxError(f"Cannot use 'L' suffix on floating-point number at line {start_line}")
                is_long = True
                value += suffix
                self.advance()
            elif suffix in ['F', 'f']:
                is_float = True
                is_double = False
                value += suffix
                self.advance()
            elif suffix in ['D', 'd']:
                is_double = True
                is_float = False
                value += suffix
                self.advance()
            else:
                suffix = ''
        
        if is_long:
            self.add(Token('LONG_LITERAL', value, start_line, start_col, self.position))
        elif is_float:
            self.add(Token('FLOAT_LITERAL', value, start_line, start_col, self.position))
        elif is_double or ((has_decimal_point or has_exponent) and not suffix):
            self.add(Token('DOUBLE_LITERAL', value, start_line, start_col, self.position))
        else:
            self.add(Token('INT_LITERAL', value, start_line, start_col, self.position))
    def parseChar(self):
        start_line = self.line
        start_col = self.column
        self.advance()
        if self.position >= len(self.fileCode):
            raise SyntaxError(f"Unclosed character literal at line {start_line}")
        if self.getCharAt() == '\\':
            self.advance()
            if self.position >= len(self.fileCode):
                raise SyntaxError(f"Unclosed escape sequence at line {start_line}")
            
            char = self.getCharAt()
            if char == 'n':
                value = '\n'
            elif char == 't':
                value = '\t'
            elif char == '\\':
                value = '\\'
            elif char == "'":
                value = "'"
            elif char == '"':
                value = '"'
            elif char == 'r':
                value = '\r'
            else:
                raise SyntaxError(f"Invalid escape sequence '\\{char}' at line {start_line}")
            self.advance()
        else:
            value = self.getCharAt()
            self.advance()
        if self.position >= len(self.fileCode) or self.getCharAt() != "'":
            raise SyntaxError(f"Unclosed character literal at line {start_line}")
        
        self.advance()
        
        self.add(Token('CHAR_LITERAL', value, start_line, start_col, self.position))
    def parseString(self):
        start_line = self.line
        start_col = self.column
        self.advance()
        value = ""
        while self.position < len(self.fileCode):
            char = self.getCharAt()
            
            if char == '"':
                self.advance()
                self.add(Token('STRING_LITERAL', value, start_line, start_col, self.position))
                return
            
            if char == '\\':
                self.advance()
                if self.position >= len(self.fileCode):
                    raise SyntaxError(f"Unclosed escape sequence in string at line {start_line}")
                
                escape_char = self.getCharAt()
                if escape_char == 'n':
                    value += '\n'
                elif escape_char == 't':
                    value += '\t'
                elif escape_char == '\\':
                    value += '\\'
                elif escape_char == '"':
                    value += '"'
                elif escape_char == 'r':
                    value += '\r'
                elif escape_char == 'b':
                    value += '\b'
                elif escape_char == 'f':
                    value += '\f'
                else:
                    value += '\\' + escape_char
                self.advance()
            else:
                value += char
                self.advance()
        
        raise SyntaxError(f"Unclosed string literal at line {start_line}")
    def parseIdentifier(self):
        value = ""

        start_line = self.line
        start_col = self.column
        while (self.getCharAt().isalnum() or self.getCharAt() == '_'):
            value += self.getCharAt()
            self.advance()
        
        if value in EvalTokens.TOKENS:
            self.add(Token(EvalTokens.TOKENS[value], value, start_line, start_col, self.position))
        else:
            self.add(Token('IDENTIFIER', value, start_line, start_col, self.position))
    def get(self):
        return self.tokens
    def getSource(self) -> str:
        return self.fileCode
    @staticmethod
    def parseDigitString(digitStr: str) -> str:
        if not digitStr:
            raise ValueError("Empty digit string")
        suffix = digitStr[-1].lower()
        if suffix == 'l':
            if '.' in digitStr or 'e' in digitStr.lower():
                raise ValueError(f"Cannot use 'L' suffix on floating-point number: '{digitStr}'")
            return 'LONG_LITERAL'
        if suffix == 'f':
            return 'FLOAT_LITERAL'
        if suffix == 'd':
            return 'DOUBLE_LITERAL'
        if '.' in digitStr or 'e' in digitStr.lower():
            return 'DOUBLE_LITERAL'
        else:
            return 'INT_LITERAL'

ACCESS_MODIFIERS = [EvalTokens.TOKENS['public'], EvalTokens.TOKENS['private'], EvalTokens.TOKENS['protected'], EvalTokens.TOKENS['default']]
RETURN_TYPES = [EvalTokens.TOKENS['byte'], EvalTokens.TOKENS['short'], EvalTokens.TOKENS['int'],
EvalTokens.TOKENS['long'], EvalTokens.TOKENS['char'], EvalTokens.TOKENS['String'], EvalTokens.TOKENS['boolean'], EvalTokens.TOKENS['float'], EvalTokens.TOKENS['double']]
OPERATORS = [EvalTokens.TOKENS['+'], EvalTokens.TOKENS['-'], EvalTokens.TOKENS['*'], EvalTokens.TOKENS['/'], EvalTokens.TOKENS['%']]
BOOL_OPERATORS = [EvalTokens.SINGLE_CHAR_OP['>'], EvalTokens.SINGLE_CHAR_OP['<'], EvalTokens.TWO_CHAR_OP['&&'], EvalTokens.TWO_CHAR_OP['||'], EvalTokens.TWO_CHAR_OP['>='],
EvalTokens.TWO_CHAR_OP['<='], EvalTokens.SINGLE_CHAR_OP['!'], EvalTokens.TWO_CHAR_OP['=='], EvalTokens.TWO_CHAR_OP['!=']]
LOOP_ACTIONS = [EvalTokens.TOKENS['break'], EvalTokens.TOKENS['continue']]
RETURN_TYPE_STR = ('String', 'char', 'byte', 'short', 'int', 'long')

def parseTokenAsType(token: str, acceptVoid: bool = False) -> object:
    match token:
        case 'IDENTIFIER':
            return type(resolveOperand(None, None, token))
        case 'BYTE_TYPE' | 'byte' | 'BYTE_LITERAL':
            return Byte
        case 'SHORT_TYPE' | 'short' | 'SHORT_LITERAL':
            return Short
        case 'INT_TYPE' | 'int' | 'INT_LITERAL':
            return Int
        case 'LONG_TYPE' | 'long' | 'LONG_LITERAL':
            return Long
        case 'CHAR_TYPE' | 'char' | 'CHAR_LITERAL':
            return Char
        case 'STRING_TYPE' | 'String' | 'STRING_LITERAL':
            return String
        case 'BOOLEAN_TYPE' | 'boolean' | 'bool' | 'BOOL_LITERAL':
            return Bool
        case 'VOID_TYPE' | 'void':
            if not acceptVoid:
                raise ValueError(f"Unexpected void type token: '{token}'")
            return Void
        case 'FLOAT_TYPE' | 'float' | 'FLOAT_LITERAL':
            return Float
        case 'DOUBLE_TYPE' | 'double' | 'DOUBLE_LITERAL':
            return Double
        case 'STRING_TYPE' | 'String' | 'STRING_LITERAL':
            return String
        case _:
            if isClass(token):
                return ClassType
            else:
                raise ValueError(f"Unknown type token: '{token}'")
def prettyPrint(this: dict):
    import json
    print(json.dumps(this, indent=4,default=str))
def getArgValById(args: list, nameOfArg: str, methodName: str, className: str):
    # Returns argument val based on the argument id (the name of the argument)
    methodArgs: dict = {}
    if className in memory:
        search = memory
    elif className in interfaces:
        search = interfaces
    for _, method in list(search[className]['methods'].items()):
        if method['name'] == methodName:
            methodArgs = method['args']
    argPos = list(methodArgs.keys())
    if len(argPos) < len(args):
        raise RuntimeError(f'Method {methodName} of class {className} got too many arguments')
    elif len(argPos) > len(args):
        raise RuntimeError(f'Method {methodName} of class {className} got too little arguments')
    else:
        return args[argPos.index(nameOfArg)]
def toRPN(tokens: TokenSlice) -> TokenSlice:
    output: list = []
    opStack: list = []
    UNARY_OPS = {'LOGICAL_NOT', 'INC', 'DEC'}
    
    for i, tok in enumerate(tokens):
        t = tok.get()['type']
        if t in ('RESOLVED_VALUE', 'INT_LITERAL', 'LONG_LITERAL', 'BYTE_LITERAL',
                 'SHORT_LITERAL', 'FLOAT_LITERAL', 'DOUBLE_LITERAL', 'STRING_LITERAL',
                 'IDENTIFIER', 'TRUE', 'FALSE'):
            output.append(tok)
            continue
        
        if t == 'LPAREN':
            opStack.append(tok)
            continue
        if t == 'RPAREN':
            while opStack and opStack[-1].get()['type'] != 'LPAREN':
                output.append(opStack.pop())
            if not opStack:
                raise SyntaxError('Mismatched parentheses')
            opStack.pop()
            continue
        if t == 'MINUS':
            is_unary = (i == 0) or (tokens[i-1].get()['type'] in 
                ('LPAREN', 'PLUS', 'MINUS', 'MULTIPLY', 'DIVIDE', 'MODULO',
                 'LOGICAL_NOT', 'INC', 'DEC', 'ASSIGN', 'COMMA',
                 'LOGICAL_AND', 'LOGICAL_OR', 'GREATER', 'LESS',
                 'EQUALS', 'NOT_EQUALS', 'GREATER_EQUAL', 'LESS_EQUAL'))
            if is_unary:
                while opStack and opStack[-1].get()['type'] in ('LOGICAL_NOT', 'INC', 'DEC', 'UNARY_MINUS'):
                    output.append(opStack.pop())
                opStack.append(Token('UNARY_MINUS', '-', tok.line, tok.column, tok.truePos))
                continue
        if t in UNARY_OPS:
            while opStack and opStack[-1].get()['type'] in UNARY_OPS:
                output.append(opStack.pop())
            opStack.append(tok)
            continue
        if t in EvalTokens.PRECEDENCE:
            while (opStack and opStack[-1].get()['type'] in EvalTokens.PRECEDENCE and
                   EvalTokens.PRECEDENCE[opStack[-1].get()['type']] >= EvalTokens.PRECEDENCE[t]):
                output.append(opStack.pop())
            opStack.append(tok)
            continue
        raise SyntaxError(f'Unexpected token in expression: {t} ({tok.get()["val"]})')
    while opStack:
        top = opStack.pop()
        if top.get()['type'] in ('LPAREN', 'RPAREN'):
            raise SyntaxError('Mismatched parentheses')
        output.append(top)
    
    return output
NUMERIC_RANK = [Byte, Short, Int, Long, Float, Double]
ARITHMETIC_OPS = {'PLUS','MINUS','MULTIPLY','DIVIDE','MODULO'}
def promote(a: Returnable, b: Returnable):
    return type(a) if NUMERIC_RANK.index(type(a)) >= NUMERIC_RANK.index(type(b)) else type(b)
BIN_OPS = {
    'PLUS': lambda a, b: a + b,
    'MINUS': lambda a, b: a - b,
    'MULTIPLY': lambda a, b: a * b,
    'DIVIDE': lambda a, b: float(a / b),
    'MODULO': lambda a, b: a % b,
    'GREATER': pyop.gt, 'LESS': pyop.lt,
    'GREATER_EQUAL': pyop.ge, 'LESS_EQUAL': pyop.le,
    'EQUALS': pyop.eq, 'NOT_EQUALS': pyop.ne,
    'LOGICAL_AND': lambda a, b: a and b,
    'LOGICAL_OR': lambda a, b: a or b,
    'LOGICAL_NOT': lambda a: not a,
}
ONLY_SINGLE_ARG = [
    'LOGICAL_NOT',
]

def matchingParen(tokens: TokenSlice, openIdx: int) -> int:
    depth = 0
    for i in range(openIdx, len(tokens)):
        t = tokens[i].get()['type']
        if t == 'LPAREN': 
            depth += 1
        if t == 'RPAREN':
            depth -= 1
            if depth == 0: 
                return i
    raise SyntaxError('Unmatched parenthesis')
def matchingBracket(tokens: TokenSlice, openIdx: int) -> int:
    depth = 0
    for i in range(openIdx, len(tokens)):
        t = tokens[i].get()['type']
        if t == 'LBRACKET':
            depth += 1
        elif t == 'RBRACKET':
            depth -= 1
            if depth == 0:
                return i
    raise SyntaxError('Unmatched bracket')
def matchingBrace(tokens: TokenSlice, openIdx: int) -> int:
    depth = 1
    j = openIdx + 1
    while j < len(tokens):
        tt = tokens[j].get()['type']
        if tt == 'LBRACE':
            depth += 1
        elif tt == 'RBRACE':
            depth -= 1
            if depth == 0:
                return j
        j += 1
    raise SyntaxError("Unmatched '{' in array initializer")

def resolveDotChain(me: 'StackFrame | None', methodArgs: 'list | None', tokens: TokenSlice, startIdx: int):
    leftToken = tokens[startIdx]
    leftName = leftToken.get()['val']

    if leftName == 'this':
        if me is None or me.this is None:
            raise RuntimeError("Cannot use 'this' in static context")
        current = me.this
    elif leftName == 'super':
        # super refers to the superclass of the current class
        if me is None or me.this is None:
            raise RuntimeError("Cannot use 'super' in static context")
        current = me.this
        superclass_name = getSuperOfClass(me.class_name)
    elif leftName in memory:
        current = leftName
    else:
        current = resolveValue(me, methodArgs, leftToken)

    j = startIdx + 1
    # Initialize className for the current object
    if isinstance(current, str):
        className = current
    elif isinstance(current, ObjectReference):
        className = current.getClass()
    else:
        className = None

    while j < len(tokens) and tokens[j].get()['type'] == 'DOT':
        if j + 1 >= len(tokens) or tokens[j + 1].get()['type'] != 'IDENTIFIER':
            raise SyntaxError("Expected identifier after '.'")
        memberName = tokens[j + 1].get()['val']
        isCall = j + 2 < len(tokens) and tokens[j + 2].get()['type'] == 'LPAREN'
        if className in memory and memberName in memory[className]['methods']:
            method_info = memory[className]['methods'][memberName]
            if not method_info.get('static', False) and not className:
                raise RuntimeError(f"Method '{memberName}' in class '{className}' is not static")
        callerClass = me.class_name if me is not None else (className if className else '')

        is_super_access = (leftName == 'super' and j == startIdx + 1)

        if isinstance(current, str) and not isCall:
            if memberName in staticVariables.get(current, {}):
                field_data = staticVariables[current][memberName]
                thisScope = perspectiveOfClass(callerClass, current)
                isAllowedAtThisScope(field_data['modifier'], thisScope, field_data.get('isInterface', False))
                current = field_data['value']
                j += 2
                continue
            else:
                raise RuntimeError(f"Static field '{memberName}' not found in class '{current}'")
        elif isinstance(current, PrimitiveArray): #arr.length
            if memberName == 'length':
                current = Int(current.size)
                j += 2
                continue
            else:
                raise RuntimeError(f"Primitive array has no field '{memberName}'")
        elif isinstance(current, ObjectReference):
            className = current.getClass()
            
            if isCall:
                openParenIdx = j + 2
                closeIdx = matchingParen(tokens, openParenIdx)
                argGroups = Token.splitArgs(tokens[openParenIdx + 1 : closeIdx])
                evaledArgs = [Expression.evaluate(me, methodArgs, g) for g in argGroups]
                thisRef = current if isinstance(current, ObjectReference) else None
                
                if is_super_access:
                    start_class = superclass_name
                else:
                    start_class = None
                
                current = invokeMethod(className, memberName, evaledArgs, caller=callerClass, thisRef=thisRef, startClass=start_class)
                j = closeIdx + 1
            else:
                # Field access on object
                if is_super_access:
                    field_class = superclass_name
                else:
                    field_class = className
                
                thisScope = perspectiveOfClass(callerClass, field_class)
                # Check if field exists
                if field_class in memory and memberName in memory[field_class]['fields']:
                    isAllowedAtThisScope(memory[field_class]['fields'][memberName]['modifier'], thisScope)
                    current = current.get().fields.get(memberName, Null())
                else:
                    raise RuntimeError(f"Field '{memberName}' not found in class '{field_class}'")
                j += 2
                continue
        # Main.add()
        elif isinstance(current, str) and isCall:
            className = current
            openParenIdx = j + 2
            closeIdx = matchingParen(tokens, openParenIdx)
            argGroups = Token.splitArgs(tokens[openParenIdx + 1 : closeIdx])
            evaledArgs = [Expression.evaluate(me, methodArgs, g) for g in argGroups]
            
            if className in memory and memberName in memory[className]['methods']:
                method_info = memory[className]['methods'][memberName]
                if not method_info.get('static', False):
                    raise RuntimeError(f"Method '{memberName}' in class '{className}' is not static")
                
                thisScope = perspectiveOfClass(callerClass, className)
                isAllowedAtThisScope(method_info['modifier'], thisScope)
                current = invokeMethod(className, memberName, evaledArgs, caller=callerClass, thisRef=None)
                j = closeIdx + 1
            elif isInterface(className):
                method_info = interfaces[className]['methods'][memberName]
                if method_info.get('modifier') not in ['public', 'default']:
                    # Only check if it's not public/default (which shouldn't happen for interfaces)
                    thisScope = perspectiveOfClass(callerClass, className)
                    isAllowedAtThisScope(method_info['modifier'], thisScope, isInterface=True)
                current = invokeMethod(className, memberName, evaledArgs, caller=callerClass, thisRef=None, isInterfaceMethod=True)
                j = closeIdx + 1
            else:
                raise RuntimeError(f"Static method '{memberName}' not found in class '{className}'")
        
        elif isinstance(current, ExceptionValue):
            if memberName == 'getMessage' and isCall:
                openParenIdx = j + 2
                closeIdx = matchingParen(tokens, openParenIdx)
                current = String(current.message)
                j = closeIdx + 1
                continue
            else:
                raise RuntimeError(f"Exception value has no member '{memberName}'")
        else:
            raise RuntimeError(f"Cannot access '.{memberName}' on value: {current!r}")

    return current, j
def collapseTokenSlice(me: StackFrame | None, methodArgs: list | None, tokens: TokenSlice) -> TokenSlice:
    out, i = [], 0
    while i < len(tokens):
        t = tokens[i].get()['type']
        if t == 'NEW':
            if i + 2 < len(tokens) and tokens[i + 2].get()['type'] == 'LBRACKET':
                element_type_token = tokens[i + 1].get()['val']
                element_type = parseTokenAsType(element_type_token)

                close_bracket = matchingBracket(tokens, i + 2)

                declared_size = None
                if close_bracket > i + 3:
                    size_tokens = tokens[i + 3:close_bracket]
                    size_value = Expression.evaluate(me, methodArgs, size_tokens)
                    if not isinstance(size_value, Int):
                        raise RuntimeError("Array size must be an integer")
                    declared_size = size_value.get()

                next_idx = close_bracket + 1

                if next_idx < len(tokens) and tokens[next_idx].get()['type'] == 'LBRACE':
                    close_brace = matchingBrace(tokens, next_idx)
                    elem_groups = Token.splitArgs(tokens[next_idx + 1:close_brace])
                    initial_values = []
                    for group in elem_groups:
                        if not group:
                            continue
                        val = Expression.evaluate(me, methodArgs, group)
                        initial_values.append(convertValue(val, element_type))

                    size = declared_size if declared_size is not None else len(initial_values)
                    if len(initial_values) > size:
                        raise RuntimeError(
                            f"Array initializer has {len(initial_values)} elements, exceeds declared size {size}"
                        )
                    array_obj = newPrimitiveArray(element_type, size, initial_values)
                    i = close_brace + 1
                else:
                    size = declared_size if declared_size is not None else 0
                    array_obj = newPrimitiveArray(element_type, size)
                    i = next_idx

                out.append(Token.wrap(array_obj))
                continue
            else:
                className = tokens[i+1].get()['val']
                closeIdx = matchingParen(tokens, i + 2)
                out.append(Token.wrap(newObject(className)))
                i = closeIdx + 1
                continue
        elif t in ('IDENTIFIER', 'THIS', 'SUPER') and i + 1 < len(tokens) and tokens[i + 1].get()['type'] == 'DOT': # Complex call
            resolved, nextIdx = resolveDotChain(me, methodArgs, tokens, i)
            out.append(Token.wrap(resolved))
            i = nextIdx
            continue
        elif t == 'LPAREN' and i + 2 < len(tokens) and tokens[i+1].get()['type'] in RETURN_TYPES and tokens[i+2].get()['type'] == 'RPAREN' and (i == 0 or tokens[i-1].get()['type'] not in ('IDENTIFIER', 'RPAREN', 'RBRACKET')): # Casting
            # (<type>) <value> <...?>;
            castToType = tokens[i+1].get()['type']
            i += 3 # skip (<type>)
            parenDepth = 0
            castValues = []
            for tok in tokens[i:]:
                if tok.get()['val'] == '(':
                    parenDepth += 1
                elif tok.get()['val'] == ')':
                    parenDepth -= 1
                if tok.get()['type'] in OPERATORS and parenDepth == 0: # Has ended
                    break 
                elif tok.get()['val'] == ';':
                    if parenDepth > 0:
                        raise SyntaxError(f'Could not resolved {parenDepth} parenthesis in the casting expression')
                    break
                castValues.append(tok)
            castValue = Expression.evaluate(me, me.getArgs(), castValues)
            finalCastValue = convertValue(castValue, parseTokenAsType(castToType), allowLossy=True)
            out.append(Token.wrap(finalCastValue))
            i += len(castValues)
            continue
        elif t == 'IDENTIFIER' and i + 1 < len(tokens) and tokens[i + 1].get()['type'] == 'LBRACKET':
            array_name = tokens[i].get()['val']
            close_idx = matchingBracket(tokens, i + 1)
            index_tokens = tokens[i + 2 : close_idx]
            index_value = Expression.evaluate(me, methodArgs, index_tokens)
            array_obj = resolveValue(me, methodArgs, tokens[i])
            
            if not isinstance(array_obj, PrimitiveArray):
                raise RuntimeError(f"'{array_name}' is not an array")
            
            element = array_obj.get(index_value.get())
            out.append(Token.wrap(element))
            i = close_idx + 1
            continue
                # In collapseTokenSlice, around line 1445-1455:
        elif t == 'IDENTIFIER' and i + 1 < len(tokens) and tokens[i+1].get()['type'] == 'LPAREN':
            methodName = tokens[i].get()['val']
            method_handled = False
            if me is not None:
                method_info = memory.get(me.class_name, {}).get('methods', {}).get(methodName)
                if method_info and method_info.get('static', False):
                    open_paren = i + 1
                    close_paren = matchingParen(tokens, open_paren)
                    
                    arg_tokens = tokens[open_paren + 1 : close_paren]
                    arg_groups = Token.splitArgs(arg_tokens)
                    evaled_args = [Expression.evaluate(me, methodArgs, group) for group in arg_groups]
                    
                    result = invokeMethod(me.class_name, methodName, evaled_args, caller=me.class_name)
                    out.append(Token.wrap(result))
                    i = close_paren + 1
                    method_handled = True
                    continue

                if method_info and not method_info.get('static', False) and me.this is not None:
                    # Unqualified instance method call (implicit 'this.method()') -- this also
                    # covers interface default methods calling other instance methods unqualified,
                    # since default method bodies are copied into the implementing class's own
                    # 'methods' table with 'this' bound to the actual instance.
                    open_paren = i + 1
                    close_paren = matchingParen(tokens, open_paren)

                    arg_tokens = tokens[open_paren + 1 : close_paren]
                    arg_groups = Token.splitArgs(arg_tokens)
                    evaled_args = [Expression.evaluate(me, methodArgs, group) for group in arg_groups]

                    result = invokeMethod(me.class_name, methodName, evaled_args, caller=me.class_name, thisRef=me.this)
                    out.append(Token.wrap(result))
                    i = close_paren + 1
                    method_handled = True
                    continue
                
                if me.class_name in interfaces:
                    interface_info = interfaces[me.class_name]
                    if methodName in interface_info['methods']:
                        method_info = interface_info['methods'][methodName]
                        if method_info.get('static', False):
                            open_paren = i + 1
                            close_paren = matchingParen(tokens, open_paren)
                            
                            arg_tokens = tokens[open_paren + 1 : close_paren]
                            arg_groups = Token.splitArgs(arg_tokens)
                            evaled_args = [Expression.evaluate(me, methodArgs, group) for group in arg_groups]
                            
                            result = invokeMethod(me.class_name, methodName, evaled_args, caller=me.class_name, isInterfaceMethod=True)
                            out.append(Token.wrap(result))
                            i = close_paren + 1
                            method_handled = True
                            continue
                
                if hasattr(me, 'class_name') and me.class_name in interfaces:
                    for iface_name in [me.class_name] + getHierarchyOfInterface(me.class_name):
                        if iface_name in interfaces and methodName in interfaces[iface_name]['methods']:
                            method_info = interfaces[iface_name]['methods'][methodName]
                            if method_info.get('static', False):
                                open_paren = i + 1
                                close_paren = matchingParen(tokens, open_paren)
                                
                                arg_tokens = tokens[open_paren + 1 : close_paren]
                                arg_groups = Token.splitArgs(arg_tokens)
                                evaled_args = [Expression.evaluate(me, methodArgs, group) for group in arg_groups]
                                
                                result = invokeMethod(iface_name, methodName, evaled_args, caller=me.class_name, isInterfaceMethod=True)
                                out.append(Token.wrap(result))
                                i = close_paren + 1
                                method_handled = True
                                break
            
            if not method_handled:
                out.append(tokens[i])
                i += 1
            continue
        elif t == 'IDENTIFIER':
            if i + 1 < len(tokens) and tokens[i + 1].get()['type'] == 'LPAREN':
                out.append(tokens[i])
                i += 1
            else:
                if me is None:
                    raise RuntimeError(f"Cannot resolve '{tokens[i].get()['val']}' in this context")
                out.append(Token.wrap(resolveValue(me, methodArgs, tokens[i])))
                i += 1
        elif t == 'THIS':
            if me is None or me.this is None:
                raise RuntimeError("Cannot use 'this' in static context")
            out.append(Token.wrap(me.this))
            i += 1
        else:
            out.append(tokens[i])
            i += 1
    return out

def resolveValue(me: StackFrame | None, methodArgs: list | None, tok: Token):
    name = tok.get()['val']
    foundRetValue = None
    try:
        foundRetValue = me.getLocal(name, Null(), True)['value']
    except RuntimeError:
        pass

    if foundRetValue is None:
        try:
            foundRetValue = getArgValById(methodArgs, name, me.method_name, me.class_name)
        except ValueError:
            pass
    isVarStatic = name in staticVariables.get(me.class_name, {})
    if foundRetValue is None:
        if me.this is not None and not isVarStatic:
            instance_obj = me.this.get()
            if name in instance_obj.fields:
                foundRetValue = instance_obj.fields[name]
        elif isVarStatic:
            foundRetValue = staticVariables[me.class_name][name]['value']
        else:
            if isClass(name):
                foundRetValue = ClassReference(name)
    if foundRetValue is None:
        if isInterface(name):
            foundRetValue = name
            
    if foundRetValue is None:
        raise RuntimeError(f"'{name}' is not a local variable, argument, field, or static member of '{me.class_name}'")
    elif isinstance(foundRetValue, Null):
        raise RuntimeError(f'NullPointerException: {tok}')
    else:
        return foundRetValue
def resolveOperand(me: StackFrame | None, methodArgs: list | None, tok: Token | str):
    if isinstance(tok, (Byte, Short, Int, Long, Bool, Float, Double, Char, String, Null)):
        return tok
    if isinstance(tok, str):
        try:
            if tok.isdigit():
                return Int(int(tok))
            elif tok.lower() == 'true':
                return Bool(True)
            elif tok.lower() == 'false':
                return Bool(False)
            elif tok.endswith(('L', 'l')):
                return Long(int(tok[:-1]))
            elif tok.endswith(('F', 'f')):
                return Float(float(tok[:-1]))
            elif tok.endswith(('D', 'd')):
                return Double(float(tok[:-1]))
            elif tok.lower() == 'null':
                return Null()
            else:
                if me is not None and methodArgs is not None:
                    temp_token = Token('IDENTIFIER', tok, 0, 0, 0)
                    return resolveValue(me, methodArgs, temp_token)
                else:
                    return tok
        except (ValueError, TypeError):
            return tok
    if not isinstance(tok, Token):
        raise TypeError(f"Expected Token or str, got {type(tok)}")
    t = tok.get()['type']
    val = tok.get()['val']
    if t == 'RESOLVED_VALUE':
        return tok.value
    if t == 'INT_LITERAL':
        try:
            return Int(int(val))
        except ValueError:
            raise SyntaxError(f"Invalid integer literal: '{val}'")
    if t == 'LONG_LITERAL':
        val_str = str(val)
        if not val_str.endswith(('L', 'l')):
            raise RuntimeError('LONG_LITERAL expects suffix "L" in definition')
        try:
            return Long(int(val_str[:-1]))
        except ValueError:
            raise SyntaxError(f"Invalid long literal: '{val}'")
    if t == 'BYTE_LITERAL':
        try:
            return Byte(int(val))
        except ValueError:
            raise SyntaxError(f"Invalid byte literal: '{val}'")
    if t == 'SHORT_LITERAL':
        try:
            return Short(int(val))
        except ValueError:
            raise SyntaxError(f"Invalid short literal: '{val}'")
    if t == 'FLOAT_LITERAL':
        val_str = str(val)
        if val_str.endswith(('f', 'F')):
            val_str = val_str[:-1]
        try:
            return Float(float(val_str))
        except ValueError:
            raise SyntaxError(f"Invalid float literal: '{val}'")
    if t == 'DOUBLE_LITERAL':
        val_str = str(val)
        if val_str.endswith(('d', 'D')):
            val_str = val_str[:-1]
        try:
            return Double(float(val_str))
        except ValueError:
            raise SyntaxError(f"Invalid double literal: '{val}'")
    if t == 'TRUE' or t=='true':
        return Bool(True)
    if t == 'FALSE' or t=='false':
        return Bool(False)
    if t == 'NULL':
        return Null()
    if t == 'IDENTIFIER':
        if me is None or methodArgs is None:
            return val
        return resolveValue(me, methodArgs, tok)
    if t == 'STRING_LITERAL':
        return String(val)
    raise SyntaxError(f"Cannot resolve operand: {t} (value: '{val}')")
def convertValue(value: object, target_type: object, allowLossy: bool = False) -> object:
    if isinstance(value, target_type):
        return value

    if isinstance(target_type, type) and issubclass(target_type, Numeric) and isinstance(value, Numeric):
        raw = value.get()
        if hasattr(value, 'bits'):
            if value.get_bits() > target_type.get_bits() and not allowLossy:
                raise ValueError(f'Possible lossy conversion from {type(value).__name__} to {target_type.__name__}')
        try:
            return target_type(raw)
        except ValueError:
            raise ValueError(f'Cannot convert {type(value).__name__} to {target_type.__name__}')
    if isinstance(target_type, ClassType) and isinstance(value, ObjectReference):
        return value
    if target_type is String or target_type == String:
        if isinstance(value, (Numeric, Bool, Char)):
            return String(str(value.get()))
        elif isinstance(value, ObjectReference):
            return String(str(value))
        else:
            return String(str(value))
    raise TypeError(f"Cannot convert {type(value).__name__} to {target_type.__name__}")
def coerceValue(value: object, target_type: object) -> object:
    if not isinstance(target_type, type) or not issubclass(target_type, Numeric):
        return value
    if not isinstance(value, Numeric) or isinstance(value, target_type):
        return value
    return convertValue(value, target_type, allowLossy=True)

def validateInterfaceImplementation(className: str, interfaceName: str):
    if not isInterface(interfaceName):
        raise NameError(f"Interface '{interfaceName}' not found")
    
    class_info = memory[className]

    for thisIface in [interfaceName] + getHierarchyOfInterface(interfaceName):
        interface_info = interfaces[thisIface]

        for method_name, method_def in interface_info['methods'].items():
            if not method_def.get('abstract', True):
                continue
            
            if method_name not in class_info['methods']:
                raise NotImplementedError(f"Class '{className}' does not implement abstract method '{method_name}' from interface '{thisIface}'")

            try:
                class_method = class_info['methods'][method_name]
                if class_method.get('abstract', True) or not class_method.get('body'):
                    raise TypeError(f"Method '{method_name}' in class '{className}' cannot be abstract")
                
                if class_method['returns'] != method_def['returns']:
                    raise TypeError(
                        f"Return type mismatch for '{method_name}': "
                        f"interface expects {method_def['returns'].__name__}, "
                        f"class provides {class_method['returns'].__name__}"
                    )
            except KeyError:
                continue
        
class Expression:
    @staticmethod
    def evaluate(me: StackFrame | None, methodArgs: list | None, tokens: TokenSlice, forceType: str = 'int') -> Returnable:
        tokens = collapseTokenSlice(me, methodArgs, tokens)
        stack: list = []
        assumeType = forceType

        for tok in toRPN(tokens):
            t = tok.get()['type']
            
            if t == 'LONG_LITERAL':
                assumeType = 'long'
            elif t == 'BYTE_LITERAL':
                assumeType = 'byte'
            elif t == 'SHORT_LITERAL':
                assumeType = 'short'
            elif t == 'FLOAT_LITERAL':
                assumeType = 'float'

            # Unary operators
            if t == 'LOGICAL_NOT':
                a = stack.pop()
                result = Bool(not a.get())
                stack.append(result)
                continue
            if t == 'UNARY_MINUS':
                a = stack.pop()
                if isinstance(a, (Int, Long, Byte, Short, Float, Double)):
                    result = type(a)(-a.get())
                else:
                    result = Double(-a.get())
                stack.append(result)
                continue
            if t in ('INC', 'DEC'):
                a = stack.pop()
                stack.append(a)
                continue
            if t in EvalTokens.PRECEDENCE:
                b = stack.pop()
                a = stack.pop()
                if isinstance(a, String) or isinstance(b, String): # Concatenation
                    stack.append(String(str(a.get()) + str(b.get())))
                    continue
                else:
                    if t == 'DIVIDE':
                        if isinstance(a, (Float, Double)) or isinstance(b, (Float, Double)):
                            result = a.get() / b.get()
                            if isinstance(a, Double) or isinstance(b, Double):
                                result = Double(result)
                            else:
                                result = Float(result)
                        else:
                            result = int(a.get() / b.get())
                            result = Int(result)
                    else:
                        result = BIN_OPS[t](a.get(), b.get())
                    
                    if t in ARITHMETIC_OPS and t != 'DIVIDE':
                        result_type = promote(a, b)
                        result = result_type(result)
                    elif isinstance(result, bool):
                        result = Bool(result)
                    elif isinstance(result, float):
                        result = Double(result)
                    elif t == 'DIVIDE' and not isinstance(result, (Int, Float, Double)):
                        if isinstance(result, float):
                            result = Double(result)
                        else:
                            result = Int(result)
                    else:
                        # fallback using assumeType
                        if assumeType == 'int':
                            result = Int(result)
                        elif assumeType == 'long':
                            result = Long(result)
                        elif assumeType == 'byte':
                            result = Byte(result)
                        elif assumeType == 'short':
                            result = Short(result)
                        elif assumeType == 'float':
                            result = Float(result)
                        else:
                            result = Double(result)
                stack.append(result)
                continue
            stack.append(resolveOperand(me, methodArgs, tok))
        if len(stack) != 1:
            raise SyntaxError(f'Malformed expression: stack has {len(stack)} items')
        return stack[0]
class ArithmeticExpression:
    evaluate = staticmethod(lambda me, methodArgs, expr: Expression.evaluate(me, methodArgs, expr))
class BooleanExpression:
    evaluate = staticmethod(lambda me, methodArgs, expr: Expression.evaluate(me, methodArgs, expr))

def getHierarchyOfClass(class_name: str) -> dict[str, dict]:
    hierarchy: dict[str, dict] = {}
    current_name = class_name
    while current_name != 'Object':
        if current_name not in memory:
            raise NameError(f"Class '{current_name}' not found in memory")
        
        current_class = memory[current_name]
        hierarchy[current_name] = current_class
        super_ref = current_class.get('super')
        if super_ref is None:
            break
        current_name = super_ref.getClass()
    if 'Object' in memory:
        hierarchy['Object'] = memory['Object']
    return hierarchy
def perspectiveOfClass(_class: str, _relativeToClass: str) -> str:
    if not isClass(_class) and not isInterface(_class):
        raise NameError(f'Class or interface {_class} is not defined')
    elif not isClass(_relativeToClass) and not isInterface(_relativeToClass):
        raise NameError(f'Class or interface {_relativeToClass} is not defined') 

    if _class == _relativeToClass:
        return 'this'
    if isInterface(_class):
        hierarchy = getHierarchyOfInterface(_class)
    else:
        hierarchy = getHierarchyOfClass(_class)

    if _relativeToClass in hierarchy:
        return 'subclass'
    
    return 'other'

def getSuperOfInterface(interfaceName: str) -> list[str]:
    if not isInterface(interfaceName):
        raise NameError(f'Interface {interfaceName} does not exist')
    return interfaces[interfaceName]['super']
def getHierarchyOfInterface(interfaceName: str) -> list[str]:
    """Returns every interface transitively extended by interfaceName (not including itself)."""
    hierarchy: list[str] = []
    def visit(name: str):
        for superName in getSuperOfInterface(name):
            if superName not in hierarchy:
                hierarchy.append(superName)
                visit(superName)
    visit(interfaceName)
    return hierarchy

def getSuperOfClass(class_name: str) -> str:
    h = getHierarchyOfClass(class_name)
    return list(h.keys())[1] # <this_class>, <super_1>, <super_2>, ...
def isClass(class_name: str) -> bool:
    try:
        ClassReference(class_name)
        return True
    except Exception:
        return False
def isInterface(interfaceName: str) -> bool:
    return interfaceName in interfaces
def classOrInterfaceInfo(name: str) -> dict:
    if name in memory:
        return memory[name]
    if name in interfaces:
        return interfaces[name]
    raise NameError(f"Class or interface '{name}' is not defined")

class Return:
    @staticmethod
    def ret(me: StackFrame, methodArgs: list, valueByToken: TokenSlice):
        thisMethodInfo = classOrInterfaceInfo(me.class_name)['methods'][me.method_name]
        if thisMethodInfo['returns'] is Void:
            raise RuntimeError(f'Method "{me.method_name}" of class "{me.class_name}" has return statement, when return type is Void')
        retValue = Expression.evaluate(me, methodArgs, valueByToken)
        isConsistentTypes(retValue, thisMethodInfo['returns'])
        me.returnValue = coerceValue(retValue, thisMethodInfo['returns'])
class LocalAssignment:
    @staticmethod
    def assign(me: StackFrame, methodArgs: list, assignArgs: list, valueByToken: TokenSlice, assignToClassType: ClassReference | None = None): #TODO: If assigning to a class, must provide another arugment for the type of variable assigning to.
        valType = parseTokenAsType(assignArgs[0])
        val = Expression.evaluate(me, methodArgs, valueByToken)
        if not isinstance(val, ObjectReference):
            val = convertValue(val, valType)
        if valType is ClassType:
            # Expecting an object reference
            if not isinstance(val, ObjectReference):
                raise TypeError(f"Expected ObjectReference, got {type(val)}")
            # Check class compatibility
            if assignToClassType is None:
                raise TypeError('The variable provided does not accept ClassType or ClassReference')
            expected = assignToClassType.getClass()
            actual = val.getClass()
            if actual != expected and actual not in getHierarchyOfClass(expected):
                raise TypeError(f"Type mismatch: expected {expected}, got {actual}")
            me.setLocal(assignArgs[1], ClassType(actual), val)
        else:
            isConsistentTypes(valType, val)
            me.setLocal(assignArgs[1], valType, val)
class FieldAssignment:
    @staticmethod
    def evaluate(valueByToken: TokenSlice) -> Returnable:
        return Expression.evaluate(None, None, valueByToken)
class Method:
    def __init__(self):
        self.me = currentFrame()
        self.className = currentFrame().class_name
        self.methodName = currentFrame().method_name
        self.args = currentFrame().getArgs()
        if self.className in memory:
            self.methodInfo = memory[self.className]['methods'][self.methodName]
            self.isInterfaceMethod = False
        elif self.className in interfaces:
            self.methodInfo = interfaces[self.className]['methods'][self.methodName]
            self.isInterfaceMethod = True
        else:
            raise NameError(f"Class/Interface '{self.className}' not found")
        
        if self.methodInfo.get('abstract', False):
            raise RuntimeError(f"Cannot execute abstract method '{self.methodName}'")
        
        self.methodBody = self.methodInfo.get('body', '')
        self.tokPosition = 0
        self.isInLoop = False
        self.forLocalVar = {}
    def read(self, token: Token, character: str) -> TokenSlice:
        startRead = self.lang.index(token)
        endRead = startRead
        for tok in self.lang[startRead:]:
            if tok.get()['val'] != character:
                endRead += 1
            else:
                break
        return self.lang[startRead:endRead]
    def readUntilMatching(self, start_token: Token, open_char: str, close_char: str) -> TokenSlice: # Read nested characters
        start_idx = self.lang.index(start_token)
        end_idx = start_idx
        depth = 0
        started = False
        
        for i, tok in enumerate(self.lang[start_idx:], start=start_idx):
            val = tok.get()['val']
            if val == open_char:
                depth += 1
                started = True
            elif val == close_char:
                depth -= 1
                if depth == 0 and started:
                    end_idx = i + 1
                    break
            end_idx = i + 1
        
        return self.lang[start_idx:end_idx]
    def before(self, by: int = 1, getType: str = 'val') -> str:
        try:
            return self.lang[self.tokPosition - by].get()[getType]
        except IndexError:
            return 
    def next(self, by: int = 1, getType: str = 'val') -> str:
        try:
            return self.lang[self.tokPosition + by].get()[getType]
        except IndexError:
            return
    def peek(self, by: int = 1) -> Token:
        return self.lang[self.tokPosition + by]
    def parse(self):
        if not self.methodBody:
            print(f'[WARNING]: Method {self.methodName} of class {self.className} has no body to execute')
        self.methodParse = Intepreter(self.methodBody)
        self.methodParse.parse()
        self.lang = self.methodParse.get() 
    def executeBlock(self):
        depth = 1
        while self.tokPosition < len(self.lang):
            t = self.lang[self.tokPosition].get()['type']
            if t == 'START_DECLARATION':
                depth += 1
                self.tokPosition += 1
            elif t == 'END_DECLARATION':
                depth -= 1
                self.tokPosition += 1
                if depth == 0:
                    break
            else:
                result = self.executeLine()
                if result == 'continue':
                    return 'continue'
                elif result == 'break':
                    return 'break'
                elif result is True:
                    return True 
        return False
    def skipBlock(self):
        depth = 1
        while self.tokPosition < len(self.lang):
            t = self.lang[self.tokPosition].get()['type']
            if t == 'START_DECLARATION':
                depth += 1
            elif t == 'END_DECLARATION':
                depth -= 1
                if depth == 0:
                    self.tokPosition += 1  # skip the '}'
                    break
            self.tokPosition += 1
    def scanBlock(self, openPos: int) -> tuple[int, int]:
        if openPos >= len(self.lang) or self.lang[openPos].get()['type'] != 'START_DECLARATION':
            raise SyntaxError("Expected '{'")
        depth = 1
        scan = openPos + 1
        while scan < len(self.lang):
            t = self.lang[scan].get()['type']
            if t == 'START_DECLARATION':
                depth += 1
            elif t == 'END_DECLARATION':
                depth -= 1
                if depth == 0:
                    return openPos + 1, scan + 1
            scan += 1
        raise SyntaxError("Unterminated block; expected '}'")
    def executeLine(self):
        if self.tokPosition >= len(self.lang):
            return False
        
        token = self.lang[self.tokPosition]
        tok_type = token.get()['type']
        tok_val = token.get()['val']

        isClassAssign = isClass(tok_val)

        if (tok_type in RETURN_TYPES or isClassAssign) and (self.next(by=2) == '=' or self.next(by=2) == ';'): # Local definition
            # <type> <name> = <value>;
            # <type> <name>;
            var_name = self.next()
            self.tokPosition += 1
            if var_name in self.me.locals:
                raise NameError(f'The variable \'{var_name}\' was already declared in this context')
            if self.next(1) == '=':
                assignToClass = ClassReference(tok_val) if isClassAssign else None
                LocalAssignment.assign(self.me, self.args, [tok_val, var_name], self.read(self.peek(2), ';'), assignToClass)
            elif self.next(1) == ';':
                self.me.setLocal(var_name, parseTokenAsType(tok_val), default_value_for_type(parseTokenAsType(tok_val)))
            return False
        elif (tok_type in RETURN_TYPES) and self.peek().get()['type'] == 'LBRACKET':
            result = ArrayAssignment.parse(self.lang, self.tokPosition, tok_type, self.me, self.args)
            if result is None:
                return False
            var_name, arrayType, array_value, self.tokPosition = result
            self.me.setLocal(var_name, PrimitiveArrayWrapper(arrayType), array_value)
            return False
        elif tok_type == 'IDENTIFIER' and self.peek().get()['type'] in ('INC', 'DEC'): # x++ or x--
            var_name = tok_val
            delta = 1 if self.peek().get()['type'] == 'INC' else -1
            self.tokPosition += 2
            if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'SEMICOLON':
                self.tokPosition += 1

            current_value = resolveValue(self.me, self.args, token)
            new_value = type(current_value)(current_value.get() + delta)
            updated = False

            # Local
            try:
                self.me.changeLocal(var_name, new_value)
                updated = True
            except RuntimeError:
                pass

            # Static
            if not updated:
                if self.me.class_name in staticVariables and var_name in staticVariables[self.me.class_name]:
                    isChangeable(staticVariables[self.me.class_name][var_name])
                    staticVariables[self.me.class_name][var_name]['value'] = new_value
                    updated = True

            # Instance field
            if not updated and self.me.this is not None:
                instance = self.me.this.get()
                if var_name in instance.fields:
                    field_def = memory[self.me.class_name]['fields'].get(var_name)
                    if field_def and field_def.get('final', False):
                        raise Exception(f"Cannot assign to final field '{var_name}'")
                    instance.fields[var_name] = new_value
                    updated = True

            # Static in super
            if not updated:
                hierarchy = getHierarchyOfClass(self.me.class_name)
                for super_name, _ in list(hierarchy.items()):
                    if super_name == self.me.class_name:
                        continue
                    if super_name in staticVariables and var_name in staticVariables[super_name]:
                        isChangeable(staticVariables[super_name][var_name])
                        staticVariables[super_name][var_name]['value'] = new_value
                        updated = True
                        break

            if not updated:
                raise RuntimeError(f"Cannot find variable '{var_name}' to increment/decrement")
            return False
        elif (tok_type == 'IDENTIFIER' or tok_type in ['THIS', 'SUPER']) and self.next(getType='type') == 'DOT': # Dot expr
            obj_token = token
            obj_name = tok_val
            self.tokPosition += 2
            
            if self.tokPosition >= len(self.lang):
                raise SyntaxError("Expected identifier after '.'")
            
            member_token = self.lang[self.tokPosition]
            if member_token.get()['type'] != 'IDENTIFIER':
                raise SyntaxError(f"Expected identifier after '.', got {member_token.get()['type']}")
            member_name = member_token.get()['val']
            if member_name == 'length' and self.next() not in ['(', ')']: # arr.length
                target = resolveValue(self.me, self.args, obj_token)
                if not isinstance(target, PrimitiveArray):
                    pass # not a primitive array 
                return target.size

            self.tokPosition += 1  # skip member name
            
            if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'LPAREN':
                # Method call
                openParenIdx = self.tokPosition
                closeIdx = matchingParen(self.lang, openParenIdx)
                argTokens = self.lang[openParenIdx + 1 : closeIdx]
                argGroups = Token.splitArgs(argTokens)
                evaledArgs = [Expression.evaluate(self.me, self.args, g) for g in argGroups]
                
                if obj_name == 'this':
                    target = self.me.this
                    if target is None:
                        raise RuntimeError("Cannot use 'this' in static context")
                elif obj_name == 'super':
                    target = self.me.this
                    if target is None:
                        raise RuntimeError("Cannot use 'super' in static context")
                    target_class = target.getClass()
                    # For super calls, start lookup from the superclass
                    start_class = memory[self.me.class_name]['super'].getClass()
                elif obj_name in memory:
                    # Static method call: ClassName.method()
                    target_class = obj_name
                    target = None
                elif obj_name in interfaces:
                    target_class = obj_name 
                    target = None
                else:
                    target = resolveValue(self.me, self.args, obj_token)
                    if not isinstance(target, ObjectReference):
                        raise RuntimeError(f"'{obj_name}' is not an object reference")
                    target_class = target.getClass()
                try:
                    start_class
                except UnboundLocalError:
                    start_class = None
                if obj_name in memory:
                    # Static
                    if member_name not in memory[obj_name]['methods']:
                        raise NameError(f"Method '{member_name}' not found in class '{obj_name}'")
                
                    if member_name not in staticMethods.get(obj_name, {}):
                        raise RuntimeError(f"Cannot call non-static method \"{member_name}\" in static context")
                    invokeMethod(
                        obj_name,
                        member_name,
                        evaledArgs,
                        caller=self.me.class_name,
                        thisRef=None
                    )
                else:
                    # Instance
                    search = memory if not isInterface(target_class) else interfaces

                    if member_name not in search[target_class]['methods']:
                        raise NameError(f"Method '{member_name}' not found in class '{target_class}'")
                    invokeMethod(
                        target_class,
                        member_name,
                        evaledArgs,
                        caller=self.me.class_name,
                        thisRef=target,
                        startClass = start_class, 
                        isInterfaceMethod = isInterface(target_class)
                    )
                self.tokPosition = closeIdx + 1
                if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'SEMICOLON':
                    self.tokPosition += 1
                return False
            else:
                if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'ASSIGN':
                    #obj.field = value
                    self.tokPosition += 1
                    rhs_tokens = []
                    while self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] != 'SEMICOLON':
                        rhs_tokens.append(self.lang[self.tokPosition])
                        self.tokPosition += 1
                    if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'SEMICOLON':
                        self.tokPosition += 1
                    else:
                        raise SyntaxError("Expected ';' after assignment")
                    new_value = Expression.evaluate(self.me, self.args, rhs_tokens)
                    
                    # Resolve
                    if obj_name in ('this', 'super'):
                        target = self.me.this
                        if target is None:
                            raise RuntimeError(f"Cannot use '{obj_name}' in static context")
                    elif obj_name in memory:
                        # Static
                        if obj_name not in staticVariables or member_name not in staticVariables[obj_name]:
                            raise NameError(f"Static variable '{member_name}' not found in class '{obj_name}'")
                        isChangeable(staticVariables[obj_name][member_name])
                        isConsistentTypes(staticVariables[obj_name][member_name]['type'], type(new_value))
                        staticVariables[obj_name][member_name]['value'] = coerceValue(new_value, staticVariables[obj_name][member_name]['type'])
                        return False
                    else:
                        target = resolveValue(self.me, self.args, obj_token)
                        if not isinstance(target, ObjectReference):
                            raise RuntimeError(f"'{obj_name}' is not an object reference")
                    
                    # Instance
                    instance = target.get()
                    if member_name not in instance.fields:
                        class_def = memory[instance.className]
                        if member_name in class_def.get('fields', {}) and class_def['fields'][member_name].get('static', False):
                            if instance.className not in staticVariables or member_name not in staticVariables[instance.className]:
                                raise RuntimeError(f"Static variable '{member_name}' not found")
                            isChangeable(staticVariables[instance.className][member_name])
                            staticVariables[instance.className][member_name]['value'] = coerceValue(new_value, staticVariables[instance.className][member_name]['type'])
                            return False
                        raise RuntimeError(f"Field '{member_name}' not found on object")
                    field_def = memory[instance.className]['fields'].get(member_name)
                    if field_def and field_def.get('final', False):
                        raise Exception(f"Cannot assign to final field '{member_name}'")
                    isConsistentTypes(field_def['type'], type(new_value))
                    instance.fields[member_name] = coerceValue(new_value, field_def['type'])
                    return False
                elif self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] in ('INC', 'DEC'):
                    # this.x++ / Object.x++ / obj.x++
                    delta = 1 if self.lang[self.tokPosition].get()['type'] == 'INC' else -1
                    self.tokPosition += 1
                    if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'SEMICOLON':
                        self.tokPosition += 1
                    else:
                        raise SyntaxError("Expected ';' after increment/decrement expression")

                    if obj_name in ('this', 'super'):
                        target = self.me.this
                        if target is None:
                            raise RuntimeError(f"Cannot use '{obj_name}' in static context")
                        instance = target.get()
                        if member_name not in instance.fields:
                            raise RuntimeError(f"Field '{member_name}' not found on object")
                        field_def = memory[instance.className]['fields'].get(member_name)
                        if field_def and field_def.get('final', False):
                            raise Exception(f"Cannot assign to final field '{member_name}'")
                        current_value = instance.fields[member_name]
                        instance.fields[member_name] = type(current_value)(current_value.get() + delta)
                    elif obj_name in memory:
                        # Static: Object.x++
                        if obj_name not in staticVariables or member_name not in staticVariables[obj_name]:
                            raise NameError(f"Static variable '{member_name}' not found in class '{obj_name}'")
                        isChangeable(staticVariables[obj_name][member_name])
                        current_value = staticVariables[obj_name][member_name]['value']
                        staticVariables[obj_name][member_name]['value'] = type(current_value)(current_value.get() + delta)
                    else:
                        target = resolveValue(self.me, self.args, obj_token)
                        if not isinstance(target, ObjectReference):
                            raise RuntimeError(f"'{obj_name}' is not an object reference")
                        instance = target.get()
                        if member_name not in instance.fields:
                            raise RuntimeError(f"Field '{member_name}' not found on object")
                        field_def = memory[instance.className]['fields'].get(member_name)
                        if field_def and field_def.get('final', False):
                            raise Exception(f"Cannot assign to final field '{member_name}'")
                        current_value = instance.fields[member_name]
                        instance.fields[member_name] = type(current_value)(current_value.get() + delta)
                    return False
                else:
                    # ???
                    self.tokPosition += 1
                    return False
        elif tok_type == 'IDENTIFIER' and self.peek().get()['type'] == 'ASSIGN' and self.before(2, getType='type') not in RETURN_TYPES: # Simple assignment
            var_name = tok_val
            self.tokPosition += 2 

            rhs_tokens = []
            while self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] != 'SEMICOLON':
                rhs_tokens.append(self.lang[self.tokPosition])
                self.tokPosition += 1
            if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'SEMICOLON':
                self.tokPosition += 1
            else:
                raise SyntaxError("Expected ';' after assignment")
            
            new_value = Expression.evaluate(self.me, self.args, rhs_tokens)
            updated = False
            # Local
            try:
                self.me.changeLocal(var_name, new_value)
                updated = True
            except RuntimeError:
                pass
            
            # Static
            if not updated:
                if self.me.class_name in staticVariables and var_name in staticVariables[self.me.class_name]:
                    isChangeable(staticVariables[self.me.class_name][var_name])
                    staticVariables[self.me.class_name][var_name]['value'] = new_value
                    updated = True
            if not updated and self.me.this is not None:
                instance = self.me.this.get()
                if var_name in instance.fields:
                    field_def = memory[self.me.class_name]['fields'].get(var_name)
                    if field_def and field_def.get('final', False):
                        raise Exception(f"Cannot assign to final field '{var_name}'")
                    isConsistentTypes(field_def['type'], type(new_value))
                    instance.fields[var_name] = coerceValue(new_value, field_def['type'])
                    updated = True
            
            # Static in super
            if not updated:
                hierarchy = getHierarchyOfClass(self.me.class_name)
                for super_name, _ in list(hierarchy.items()):
                    if super_name == self.me.class_name:
                        continue
                    if super_name in staticVariables and var_name in staticVariables[super_name]:
                        isChangeable(staticVariables[super_name][var_name])
                        staticVariables[super_name][var_name]['value'] = new_value
                        updated = True
                        break
            if not updated:
                raise RuntimeError(f"Cannot find variable '{var_name}' to assign")
            return False
        elif tok_type == 'IDENTIFIER' and self.peek().get()['type'] == 'LPAREN' and not self.before() == '.': # Simple method call
            methodName = tok_val
            openParenIdx = self.tokPosition + 1
            closeIdx = matchingParen(self.lang, openParenIdx)
            argTokens = self.lang[openParenIdx + 1 : closeIdx]
            argGroups = Token.splitArgs(argTokens)
            evaledArgs = [Expression.evaluate(self.me, self.args, g) for g in argGroups]
            
            if self.me.class_name in memory:
                if methodName in memory[self.me.class_name]['methods']:
                    method_info = memory[self.me.class_name]['methods'][methodName]
                    if method_info.get('static', False):
                        invokeMethod(
                            self.me.class_name,
                            methodName,
                            evaledArgs,
                            caller=self.me.class_name,
                            thisRef=None
                        )
                        self.tokPosition = closeIdx + 1
                        if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'SEMICOLON':
                            self.tokPosition += 1
                        return False
                    elif self.me.this is not None:
                        invokeMethod(
                            self.me.class_name,
                            methodName,
                            evaledArgs,
                            caller=self.me.class_name,
                            thisRef=self.me.this
                        )
                        self.tokPosition = closeIdx + 1
                        if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'SEMICOLON':
                            self.tokPosition += 1
                        return False
                    else:
                        raise RuntimeError(f"Cannot call non-static method \"{methodName}\" in static context")
            if self.me.class_name in interfaces:
                if methodName in interfaces[self.me.class_name]['methods']:
                    method_info = interfaces[self.me.class_name]['methods'][methodName]
                    if method_info.get('static', False) or method_info.get('modifier') == 'static':
                        invokeMethod(
                            self.me.class_name,
                            methodName,
                            evaledArgs,
                            caller=self.me.class_name,
                            isInterfaceMethod=True
                        )
                        self.tokPosition = closeIdx + 1
                        if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'SEMICOLON':
                            self.tokPosition += 1
                        return False
                    elif self.me.this is not None:
                        invokeMethod(
                            self.me.class_name,
                            methodName,
                            evaledArgs,
                            caller=self.me.class_name,
                            thisRef=self.me.this,
                            isInterfaceMethod=True
                        )
                        self.tokPosition = closeIdx + 1
                        if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'SEMICOLON':
                            self.tokPosition += 1
                        return False
                    else:
                        raise RuntimeError(f"Cannot call non-static method \"{methodName}\" in static context")
            
            raise NameError(f"Method '{methodName}' not found in class '{self.me.class_name}'")
        elif tok_type == EvalTokens.TOKENS['return']:
            Return.ret(self.me, self.args, self.read(self.peek(), ';'))
            self.tokPosition += 1
            return True
        elif tok_type == 'IF':
            self.tokPosition += 1  # jump to condition
            if self.lang[self.tokPosition].get()['type'] != 'LPAREN':
                raise SyntaxError("Expected '(' after 'if'")
            self.tokPosition += 1

            cond_tokens = []
            depth = 1
            while self.tokPosition < len(self.lang):
                t = self.lang[self.tokPosition].get()['type']
                if t == 'LPAREN':
                    depth += 1
                elif t == 'RPAREN':
                    depth -= 1
                    if depth == 0:
                        self.tokPosition += 1  # skip ')'
                        break
                cond_tokens.append(self.lang[self.tokPosition])
                self.tokPosition += 1
            
            condition = BooleanExpression.evaluate(self.me, self.args, cond_tokens)
            if self.lang[self.tokPosition].get()['type'] != 'START_DECLARATION':
                raise SyntaxError("Expected '{' after if condition")
            self.tokPosition += 1  # skip '{'
            
            if condition.get():
                result = self.executeBlock()
                if result:
                    return result
            else:
                self.skipBlock()
            
            # Else
            if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'ELSE':
                self.tokPosition += 1  # skip 'else'
                if self.lang[self.tokPosition].get()['type'] not in ['START_DECLARATION', 'IF']: # 'else {' or 'else if'
                    raise SyntaxError("Expected 'START_DECLARATION', 'IF' after else")
                self.tokPosition += 1
                if not condition.get():
                    result = self.executeBlock()
                    if result:
                        return result
                else:
                    self.skipBlock()
            
            return False
        elif tok_type == 'WHILE':
            self.tokPosition += 1
            
            if self.tokPosition >= len(self.lang) or self.lang[self.tokPosition].get()['type'] != 'LPAREN':
                raise SyntaxError("Expected '(' after while")
            self.tokPosition += 1
            
            cond_tokens = []
            depth = 1
            while self.tokPosition < len(self.lang):
                t = self.lang[self.tokPosition].get()['type']
                if t == 'LPAREN':
                    depth += 1
                elif t == 'RPAREN':
                    depth -= 1
                    if depth == 0:
                        self.tokPosition += 1
                        break
                cond_tokens.append(self.lang[self.tokPosition])
                self.tokPosition += 1
            if self.tokPosition >= len(self.lang) or self.lang[self.tokPosition].get()['type'] != 'START_DECLARATION':
                raise SyntaxError("Expected '{' after while condition")
            self.tokPosition += 1
            loop_start = self.tokPosition

            # Pre-scan to find the position right after the matching '}' so we can
            # reliably resume execution there regardless of how the loop terminates
            # (normal condition failure, break, or an early method return).
            scan = loop_start
            depth = 1
            closed = False
            while scan < len(self.lang):
                t = self.lang[scan].get()['type']
                if t == 'START_DECLARATION':
                    depth += 1
                elif t == 'END_DECLARATION':
                    depth -= 1
                    if depth == 0:
                        scan += 1
                        closed = True
                        break
                scan += 1
            if not closed:
                raise SyntaxError("Expected '}' to close 'while' block")
            after_while_pos = scan

            self.isInLoop = True
            while True:
                condition = BooleanExpression.evaluate(self.me, self.args, cond_tokens)
                if not condition.get():
                    break
                
                self.tokPosition = loop_start
                state = self.executeBlock()
                
                if state == 'break':
                    break
                elif state == 'continue':
                    continue
                elif state is True:
                    self.isInLoop = False
                    return True
            self.isInLoop = False
            self.tokPosition = after_while_pos
            return False
        elif tok_type == 'DO':
            # do { <block> } while (<condition>);
            self.tokPosition += 1
            if self.tokPosition >= len(self.lang) or self.lang[self.tokPosition].get()['type'] != 'START_DECLARATION':
                raise SyntaxError("Expected '{' after 'do'")
            self.tokPosition += 1
            loop_start = self.tokPosition

            # Locate the matching '}' for the do-block (without executing it) so we can
            # parse the trailing 'while (<condition>);' clause once, up front.
            scan = loop_start
            depth = 1
            closed = False
            while scan < len(self.lang):
                t = self.lang[scan].get()['type']
                if t == 'START_DECLARATION':
                    depth += 1
                elif t == 'END_DECLARATION':
                    depth -= 1
                    if depth == 0:
                        scan += 1
                        closed = True
                        break
                scan += 1
            if not closed:
                raise SyntaxError("Expected '}' to close 'do' block")

            if scan >= len(self.lang) or self.lang[scan].get()['type'] != 'WHILE':
                raise SyntaxError("Expected 'while' after 'do' block")
            scan += 1
            if scan >= len(self.lang) or self.lang[scan].get()['type'] != 'LPAREN':
                raise SyntaxError("Expected '(' after 'while' in do-while")
            scan += 1

            cond_tokens = []
            cdepth = 1
            while scan < len(self.lang):
                t = self.lang[scan].get()['type']
                if t == 'LPAREN':
                    cdepth += 1
                elif t == 'RPAREN':
                    cdepth -= 1
                    if cdepth == 0:
                        scan += 1
                        break
                cond_tokens.append(self.lang[scan])
                scan += 1

            if scan >= len(self.lang) or self.lang[scan].get()['type'] != 'SEMICOLON':
                raise SyntaxError("Expected ';' after do-while condition")
            after_do_while_pos = scan + 1

            self.isInLoop = True
            while True:
                self.tokPosition = loop_start
                state = self.executeBlock()

                if state == 'break':
                    break
                elif state is True:
                    self.isInLoop = False
                    return True
                # 'continue' (or normal completion) falls through to the condition check,
                # matching do-while semantics where 'continue' jumps to the condition test.

                condition = BooleanExpression.evaluate(self.me, self.args, cond_tokens)
                if not condition.get():
                    break
            self.isInLoop = False
            self.tokPosition = after_do_while_pos
            return False
        elif tok_type == 'THROW_EXPR':
            # throw new <ExceptionName>(<message?>);
            self.tokPosition += 1
            if self.tokPosition >= len(self.lang) or self.lang[self.tokPosition].get()['type'] != 'NEW':
                raise SyntaxError("Expected 'new' after 'throw' (only 'throw new <Exception>(...);' is supported)")
            self.tokPosition += 1
            if self.tokPosition >= len(self.lang) or self.lang[self.tokPosition].get()['type'] != 'IDENTIFIER':
                raise SyntaxError("Expected exception type name after 'throw new'")
            excName = self.lang[self.tokPosition].get()['val']
            excType = getattr(builtins, excName, object)
            if not (isinstance(excType, type) and issubclass(excType, (Exception, Warning))):
                raise NameError(f'Invalid exception "{excName}"')
            self.tokPosition += 1
            if self.tokPosition >= len(self.lang) or self.lang[self.tokPosition].get()['type'] != 'LPAREN':
                raise SyntaxError("Expected '(' after exception type in 'throw'")
            openParen = self.tokPosition
            closeParen = matchingParen(self.lang, openParen)
            msgTokens = self.lang[openParen + 1:closeParen]
            message = ''
            if msgTokens:
                msgValue = Expression.evaluate(self.me, self.args, msgTokens)
                message = str(msgValue.get()) if hasattr(msgValue, 'get') else str(msgValue)
            self.tokPosition = closeParen + 1
            if self.tokPosition >= len(self.lang) or self.lang[self.tokPosition].get()['type'] != 'SEMICOLON':
                raise SyntaxError("Expected ';' after throw statement")
            self.tokPosition += 1
            raise excType(message)
        elif tok_type == 'TRY':
            # try { <block> } (catch (<ExceptionType> <var>) { <block> })* (finally { <block> })?
            self.tokPosition += 1
            try_body_start, scan = self.scanBlock(self.tokPosition)

            catch_clauses = []
            while scan < len(self.lang) and self.lang[scan].get()['type'] == 'CATCH':
                scan += 1
                if scan >= len(self.lang) or self.lang[scan].get()['type'] != 'LPAREN':
                    raise SyntaxError("Expected '(' after 'catch'")
                scan += 1
                if scan >= len(self.lang) or self.lang[scan].get()['type'] != 'IDENTIFIER':
                    raise SyntaxError("Expected exception type in 'catch'")
                excName = self.lang[scan].get()['val']
                excType = getattr(builtins, excName, object)
                if not (isinstance(excType, type) and issubclass(excType, (Exception, Warning))):
                    raise NameError(f'Invalid exception type "{excName}" in catch clause')
                scan += 1
                if scan >= len(self.lang) or self.lang[scan].get()['type'] != 'IDENTIFIER':
                    raise SyntaxError("Expected variable name in 'catch'")
                varName = self.lang[scan].get()['val']
                scan += 1
                if scan >= len(self.lang) or self.lang[scan].get()['type'] != 'RPAREN':
                    raise SyntaxError("Expected ')' to close 'catch' clause")
                scan += 1
                catch_body_start, scan = self.scanBlock(scan)
                catch_clauses.append({'type': excType, 'var': varName, 'start': catch_body_start})

            finally_bounds = None
            if scan < len(self.lang) and self.lang[scan].get()['type'] == 'FINALLY':
                scan += 1
                finally_body_start, scan = self.scanBlock(scan)
                finally_bounds = finally_body_start

            if not catch_clauses and finally_bounds is None:
                raise SyntaxError("Expected 'catch' or 'finally' after 'try' block")

            after_construct_pos = scan
            control_signal = False
            pending_exception = None
            try:
                self.tokPosition = try_body_start
                control_signal = self.executeBlock()
            except Exception as caughtExc:
                handled = False
                for clause in catch_clauses:
                    if isinstance(caughtExc, clause['type']):
                        handled = True
                        self.me.setLocal(clause['var'], ExceptionValue, ExceptionValue(clause['type'], str(caughtExc)))
                        self.tokPosition = clause['start']
                        control_signal = self.executeBlock()
                        del self.me.locals[clause['var']]
                        break
                if not handled:
                    pending_exception = caughtExc

            if finally_bounds is not None:
                self.tokPosition = finally_bounds
                finally_signal = self.executeBlock()
                if finally_signal:
                    control_signal = finally_signal
                    pending_exception = None

            self.tokPosition = after_construct_pos

            if pending_exception is not None:
                raise pending_exception
            if control_signal == 'break':
                return 'break'
            elif control_signal == 'continue':
                return 'continue'
            elif control_signal is True:
                return True
            return False
        elif tok_type == 'FOR':
            # for (<type?> <id> = <val>; <id_reference_condition>; <stmt?>)
            # for (<type?> <id> : <collectionID>)
            parseIdBy = 0
            if self.next(by=2,getType='type') not in RETURN_TYPES:
                parseIdBy -= 1
            if self.next(by=4+parseIdBy) == '=':
                forLoopStmt = self.read(self.peek(), ')')
                idTypeRead = self.peek(by=2+parseIdBy).get()
                semiIdxs = [i for i, tok in enumerate(forLoopStmt) if tok.get()['type'] == 'SEMICOLON']
                bounds = [-1] + semiIdxs + [len(forLoopStmt)]
                forLoopParts = [forLoopStmt[bounds[k]+1:bounds[k+1]] for k in range(len(bounds)-1)]
                
                condition = forLoopParts[1]
                try:
                    varUpdateExpr = forLoopParts[2]
                except IndexError: # There is no update statement
                    varUpdateExpr = None
                    pass 

                if idTypeRead['type'] in RETURN_TYPES:
                    idType = parseTokenAsType(idTypeRead['val'])
                    idName = self.next(by=3+parseIdBy)
                    idValue = Expression.evaluate(self.me, self.me.getArgs(), self.read(self.peek(by=5), ';'))
                    self.me.setLocal(idName, idType, idValue)
                    wasLocallyDefined = True
                else:
                    resolveValue(self.me, self.me.getArgs(), self.peek(by=3+parseIdBy)) # just check if it exists
                    wasLocallyDefined = False
                self.tokPosition += len(forLoopStmt) + 2
                if self.tokPosition >= len(self.lang) or self.lang[self.tokPosition].get()['type'] != 'START_DECLARATION':
                    raise SyntaxError("Expected '{' after for-loop header")
                self.tokPosition += 1
                body_start = self.tokPosition 

                # Pre-scan to find the position right after the matching '}' so we can
                # reliably resume execution there regardless of how the loop terminates.
                scan = body_start
                depth = 1
                closed = False
                while scan < len(self.lang):
                    t = self.lang[scan].get()['type']
                    if t == 'START_DECLARATION':
                        depth += 1
                    elif t == 'END_DECLARATION':
                        depth -= 1
                        if depth == 0:
                            scan += 1
                            closed = True
                            break
                    scan += 1
                if not closed:
                    raise SyntaxError("Expected '}' to close 'for' block")
                after_for_pos = scan

                self.isInLoop = True
                while True:
                    condition_result = BooleanExpression.evaluate(self.me, self.args, condition)
                    if not condition_result.get():
                        break
                    self.tokPosition = body_start
                    state = self.executeBlock()
                    if state == 'break':
                        break
                    elif state == 'continue':
                        pass  # Must modify!
                    elif state is True:
                        self.isInLoop = False
                        return True
                    if varUpdateExpr is not None:
                        # Increment
                        if len(varUpdateExpr) >= 2:
                            first_tok = varUpdateExpr[0]
                            second_tok = varUpdateExpr[1] if len(varUpdateExpr) > 1 else None
                            
                            if first_tok.get()['type'] == 'IDENTIFIER' and second_tok:
                                var_name = first_tok.get()['val']
                                if second_tok.get()['type'] == 'INC':
                                    current_val = self.me.getLocal(var_name, None, True)['value']
                                    new_val = type(current_val)(current_val.get() + 1)
                                    self.me.changeLocal(var_name, new_val)
                                elif second_tok.get()['type'] == 'DEC':
                                    current_val = self.me.getLocal(var_name, None, True)['value']
                                    new_val = type(current_val)(current_val.get() - 1)
                                    self.me.changeLocal(var_name, new_val)
                                else: # Default to expr
                                    newVal = Expression.evaluate(self.me, self.args, varUpdateExpr[2:])
                                    self.me.changeLocal(idName, newVal)
                if wasLocallyDefined:
                    del self.me.locals[idName]
                self.isInLoop = False
                self.tokPosition = after_for_pos
            elif self.next(by=4+parseIdBy) == ':':
                forStmt = self.read(self.peek(), ')')
                
                colon_idx = -1
                for i, tok in enumerate(forStmt):
                    if tok.get()['val'] == ':':
                        colon_idx = i
                        break
                
                if colon_idx == -1:
                    raise SyntaxError("Expected ':' in for-each loop")
                
                left_parts = forStmt[:colon_idx]
                if left_parts[0].get()['val'] == '(': # Include '(' for some reason, strip it
                    left_parts = left_parts[1:]
                if len(left_parts) != 2: 
                    raise SyntaxError("Malformed loop statement")
                type_token = left_parts[0].get()['val']
                var_name = left_parts[1].get()['val']
                var_type = parseTokenAsType(type_token)
                
                collection_expr = forStmt[colon_idx + 1:]
                collection = Expression.evaluate(self.me, self.args, collection_expr)
                
                if not isinstance(collection, PrimitiveArray):
                    raise RuntimeError(f"Cannot iterate over non-array: {type(collection)}")
                elements = collection.get()
                self.tokPosition += len(forStmt) + 2
                if self.tokPosition >= len(self.lang) or self.lang[self.tokPosition].get()['type'] != 'START_DECLARATION':
                    raise SyntaxError("Expected '{' after for-each header")
                self.tokPosition += 1
                body_start = self.tokPosition

                # Pre-scan to find the position right after the matching '}' so we can
                # reliably resume execution there regardless of how the loop terminates.
                scan = body_start
                depth = 1
                closed = False
                while scan < len(self.lang):
                    t = self.lang[scan].get()['type']
                    if t == 'START_DECLARATION':
                        depth += 1
                    elif t == 'END_DECLARATION':
                        depth -= 1
                        if depth == 0:
                            scan += 1
                            closed = True
                            break
                    scan += 1
                if not closed:
                    raise SyntaxError("Expected '}' to close 'for' block")
                after_for_pos = scan

                self.isInLoop = True
                
                for element in elements:
                    self.me.setLocal(var_name, var_type, element)
                    self.tokPosition = body_start
                    state = self.executeBlock()
                    
                    if state == 'break':
                        break
                    elif state == 'continue':
                        continue
                    elif state is True:
                        self.isInLoop = False
                        return True
                
                del self.me.locals[var_name]
                self.isInLoop = False
                self.tokPosition = after_for_pos
                
                return False
            else:
                raise RuntimeError(f'Could not evaluate iterable of {self.read(token, ")")[0].getPos()}')
        elif tok_type in LOOP_ACTIONS:  # 'break', 'continue'
            self.tokPosition += 1 
            if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'SEMICOLON':
                self.tokPosition += 1
            else:
                raise SyntaxError(f"Expected ';' after {tok_type}")
            
            if not self.isInLoop:
                raise RuntimeError(f"'{tok_type}' outside of loop")
            
            return tok_type.lower()  # 'break' or 'continue'
        elif tok_type == 'NATIVE_PRINT_STMT' or tok_type == 'NATIVE_PRINT_STMT_nline':
            if self.peek().get()['type'] == 'LPAREN':
                expr_tokens = self.readUntilMatching(self.peek(), '(', ')')
                
                inner_tokens = expr_tokens[1:-1]
                value = Expression.evaluate(self.me, self.me.getArgs(), inner_tokens)
                endChar = '\n' if tok_type == 'NATIVE_PRINT_STMT' else ''
                if hasattr(value, 'get'):
                    value = value.get() if type(value.get()).__module__ == 'builtins' else f'[INTERNAL] {type(value.get()).__name__}@{hex(id(value.get()))}'
                else:
                    if value is Void:
                        value = f'[WARNING]: Tried to print a void return type from method {self.me.method_name}'
                    else:
                        value = value

                print('[OUTPUT]: ' + str(value), end=endChar)
                self.tokPosition += len(expr_tokens) + 1  

                if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'SEMICOLON':
                    self.tokPosition += 1
            
        self.tokPosition += 1
        return False
    def execute(self):
        if not self.lang:
            return
        hasReturned = False
        self.tokPosition = 0
        while self.tokPosition < len(self.lang):
            a = self.executeLine()
            if a:
                hasReturned = True
                break
        search = memory if not self.isInterfaceMethod else interfaces
        if not hasReturned and search[self.className]['methods'][self.methodName]['returns'] is not Void:
            raise RuntimeError(f'No return statement in method "{self.methodName}"')
class Execution:
    def __init__(self, langParse: Intepreter):
        self.langParse: Intepreter = langParse
        self.langParse.parse()
        self.lang: TokenSlice = langParse.get()
        self.argumentStack: list = [] # Argument stack for actions that need values, like ArithmeticOperation, etc.
        self.mode: list[str] = [] 
        self.tokPosition = 0
        # self.mode: This is the possible mode. For example, if the parser sees a Token(RPAREN), it may be a:
        # "arg_def", "cond_def", or "arith_paren". Once more context is provided, the list should eventually narrow down to one element (usually just cleared)\

        self.currentInterface: str = ''
        self.states: dict[str, bool] = {
            'FINAL': False,
            'STATIC': False
        }
        self.currentClass: str = ''
        self.info: dict[str, Any] = {} # Memory for the parser
    def reset(self):
        global ENTRY, nextHeapId, heap, memory, staticMethods, staticVariables
        self.lang = []
        ENTRY = {'entryClass': '', 'entryMethod': ENTRY_METHOD_NAME}
        nextHeapId = 0
        memory.clear()
        memory = {'Object': {'name': 'Object'}}
        heap.clear()
        staticVariables.clear()
        staticMethods.clear()
        interfaces.clear()
    def before(self, by: int = 1, getType: str = 'val'):
        return self.lang[self.tokPosition - by].get()[getType]
    def next(self, by: int = 1, getType: str = 'val'):
        return self.lang[self.tokPosition + by].get()[getType]
    def peek(self, by: int = 1):
        return self.lang[self.tokPosition + by]
    def read(self, token: Token, character: str) -> TokenSlice:
        startRead = self.lang.index(token)
        endRead = startRead
        for tok in self.lang[startRead:]:
            if tok.get()['val'] != character:
                endRead += 1
            else:
                break
        return self.lang[startRead:endRead]
    def executeTokens(self):
        while self.tokPosition < len(self.lang):
            token = self.lang[self.tokPosition]
            tok_type = token.get()['type']
            tok_val = token.get()['val']
            is_constructor_call = False
            if self.tokPosition >= 1:
                prev_token = self.lang[self.tokPosition - 1]
                if prev_token.get()['type'] == 'NEW':
                    is_constructor_call = True
            if self.tokPosition >= 1: # Is this a constructore / "new"?
                prev_token = self.lang[self.tokPosition - 1]
                if prev_token.get()['type'] == 'NEW':
                    is_constructor_call = True
                
            if self.currentClass and not self.info.get('isInMethod', False) and 'field_def' not in self.mode: # Applies context based on 'default'
                if tok_type in RETURN_TYPES or (tok_type == 'IDENTIFIER' and tok_val in memory):
                    next_token = self.peek(1) if self.tokPosition + 1 < len(self.lang) else None
                    next_next_token = self.peek(2) if self.tokPosition + 2 < len(self.lang) else None
                    
                    if next_token and next_token.get()['type'] == 'IDENTIFIER':
                        if next_next_token and next_next_token.get()['type'] in ('ASSIGN', 'SEMICOLON', 'LBRACKET'):
                            self.mode.append('field_def')
            if tok_type == EvalTokens.TOKENS['(']:
                self.info['parenStack'] = self.info.get('parenStack', 0) + 1
            if tok_type == 'EOF':
                if self.info.get('braceStack', 0) > 0:
                    raise SyntaxError(f'Reached EOF, but there were still {self.info.get("braceStack")} unclosed braces')
                elif self.info.get('parenStack', 0) > 0:
                    raise SyntaxError(f'Reached EOF, but there were still {self.info.get("parenStack")} unclosed parentheses')
                else:
                    break
            elif tok_type == EvalTokens.TOKENS[';']: # End of statement
                self.mode = []
                self.info['endLine'] = True
                self.info['endLinePos'] = token.getPos()['line']
            elif self.info.get('endLine', False): # Inflict an error if there is a token after the endLine token, on the same line
                if tok_type == EvalTokens.TOKENS['\n']:
                    self.clear(noClearInfo=True, noClearMode=True, noClearArgStack=True)
                    self.info['endLine'] = False
                    self.info['endLinePos'] = None
                else:
                    raise SyntaxError(f'Line end position: {self.info["endLinePos"]}\nUnexpected character "{token.get()["val"]}" after endLine token {EvalTokens.TOKENS[";"]} at pos {token.getPos()["pos"]}')
            elif tok_type == EvalTokens.TOKENS['{']:
                if 'is_active_method_def' in self.mode:
                    self.mode = ['is_active_method_def', 'method_body']
                    self.handleMethodBodyContent(token)
                elif 'is_active_if_def' in self.mode:
                    self.mode = ['is_active_if_def', 'if_body']
                    #self.handleIfCall()
                elif 'is_active_else_def' in self.mode:
                    self.mode = ['is_active_else_def', 'else_body']
                    #self.handleElseCall()
                elif self.currentClass or self.currentInterface: # Classes do not contain "code", therefore just skip
                    pass
                else:
                    raise SyntaxError(f'Unexpected open brace at pos {token.getPos()}')
                self.info['braceStack'] = self.info.get('braceStack', 0) + 1
            elif tok_type == EvalTokens.TOKENS['}']:
                if self.info.get('braceStack', 0) == 0:
                    raise SyntaxError(f'Unexpected closing brace at pos {token.getPos()["pos"]}')
                self.info['braceStack'] -= 1
                if 'is_active_method_def' in self.mode and 'method_body' in self.mode:
                    self.mode = []
                elif 'is_active_if_def' in self.mode and 'if_body' in self.mode:
                    self.mode = []
                elif 'is_active_else_def' in self.mode and 'else_body' in self.mode:
                    self.mode = []
                elif self.currentClass: # End class
                    for interface_name in memory[self.currentClass]['implements']:
                        validateInterfaceImplementation(self.currentClass, interface_name)
                    self.currentClass = ''
                elif self.currentInterface:
                    self.currentInterface = ''
                else:
                    raise SyntaxError(f'Unexpected closing brace at pos {token.getPos()["pos"]}')
            elif tok_type == EvalTokens.TOKENS['import']:
                directory = [a.get()['val'] for a in self.read(token, ';')[1:]]
                filePath = ''
                for text in directory:
                    if text == '.':
                        filePath += '/'
                        continue
                    filePath += text
                filePath += '.txt' # Add .txt
                filePath = (Path(__file__).parent / 'packages' / filePath)
                try:
                    thisPackageContent = filePath.read_text('utf-8')
                except FileNotFoundError:
                    raise FileNotFoundError(f'The specified package, "{''.join(directory)}", does not exist in the packages folder')
                print(f'[INFO]: Loading package {''.join(directory)}...')
                start = time.time()
                packageExecutor = Execution(Intepreter(thisPackageContent))
                packageExecutor.executeTokens()
                end = time.time()
                print(f'[INFO]: Successfully loaded package {''.join(directory)}')
                print(f'[INFO]: Package loaded in {(round(end-start,1))* 10 **3}ms')
            elif tok_type == EvalTokens.TOKENS['class']:
                self.mode = []
                modifier = ''
                if self.tokPosition == 1:
                    modifier = 'default'
                elif self.before() not in ('public', 'private', 'protected', 'default'):
                    modifier = 'default'
                else:
                    modifier = self.before()
                isValidModifier(modifier)
                
                class_name = self.next()
                self.tokPosition += 1

                endClassDeclr = self.tokPosition + [a.get()['val'] for a in self.lang[self.tokPosition:]].index('{')
                thisClassImplements = []
                if self.next(by=3) == 'implements' or self.next() == 'implements': 
                    # Either class A extends B implements C or class A implements B
                    readByOffset = 3 if self.next(3) == 'implements' else 1
                    startRead = self.tokPosition + readByOffset
                    
                    for token in self.lang[startRead+1:endClassDeclr+1]: # skip 'implements'
                        if token.get()['val'] in (',', '{'):
                            if not isInterface(thisClassImplements[-1]):
                                raise NameError(f'Interface {thisClassImplements[-1]} does not exist')
                            continue
                        thisClassImplements.append(token.get()['val'])

                if self.next() != 'extends':  # Check the next token after class name
                    createClass(class_name, modifier, implements=thisClassImplements)
                else:
                    if self.info.get('hasSuper', False):
                        raise NameError(f'Class {class_name} cannot have multiple parent classes')
                    self.tokPosition += 1  # skip 'extends'
                    super_name = self.next()  # get superclass name
                    createClass(class_name, modifier, ClassReference(super_name), thisClassImplements)
                self.tokPosition = endClassDeclr-1
                self.currentClass = class_name
                self.clear()
            elif tok_type == EvalTokens.TOKENS['interface']:
                name = self.next()
                supers = []
                if self.next(2) == 'extends':
                    for token in self.lang[self.tokPosition+3:]:
                        if token.get()['val'] == '\n':
                            raise SyntaxError('Reached end of line, but expected more information')
                        if token.get()['val'] in (',', '{'):
                            if supers[-1] not in interfaces:
                                raise NameError(f'Interface {supers[-1]} does not exist')
                            if token.get()['val'] == ',':
                                continue
                            else:
                                break
                        supers.append(token.get()['val'])
                createInterface(name, supers)
                self.currentInterface = name
                jumpBy = len(supers) + 1 if len(supers) > 0 else 0
                self.tokPosition += 2 + jumpBy # Skip to "{"

                continue
            elif tok_type == EvalTokens.TOKENS['final']: # Sets final
                self.states['FINAL'] = True
                self.info['readBy'] = self.info.get('readBy', 1) + 1# This makes the modifier keyword 2 spaces behind
            elif tok_type == EvalTokens.TOKENS['static']: # Sets static
                self.states['STATIC'] = True
                self.info['readBy'] = self.info.get('readBy', 1) + 1 # This makes the modifier keyword 2 spaces behind
            elif tok_type == EvalTokens.TOKENS['abstract']:
                self.states['ABSTRACT'] = True
                self.info['readBy'] = self.info.get('readBy', 1) + 1
            elif tok_type in ACCESS_MODIFIERS: # Applies context: 'method_def', 'field_def'
                self.mode.extend(['method_def', 'field_def'])
            elif tok_type in RETURN_TYPES and ('field_def' in self.mode or self.next(2) == '='): # FIELD
                modifier = 'default'
                i = self.tokPosition - 1
                while i >= 0:
                    t = self.lang[i]
                    if t.get()['type'] == 'NEWLINE' or t.get()['type'] == 'SEMICOLON' or t.get()['type'] == 'START_DECLARATION':
                        break
                    if t.get()['type'] in ACCESS_MODIFIERS or t.get()['val'] in ['public', 'private', 'protected', 'default']:
                        modifier = t.get()['val']
                        break
                    
                    i -= 1
                isInterfaceField = len(self.currentInterface) > 0
                if self.next(by=2) == '=': # Looks like: <modifier> int <id> = <value>;
                    self.mode = []
                    if tok_type == 'TRUE' or tok_type == 'FALSE' or tok_type == 'BOOLEAN_TYPE':
                        t_type = Bool
                    else:
                        t_type = parseTokenAsType(tok_type)
                    self.handleFieldDefinition(t_type, modifier, interface=isInterfaceField)
                elif self.next(by=2) == ';':
                    self.mode = []
                    self.handleNullFieldDefinition(parseTokenAsType(tok_type), modifier, interface=isInterfaceField)
            elif tok_type == 'IDENTIFIER' and 'field_def' in self.mode:
                #ClassName fieldName = ...; or ClassName fieldName;
                if self.tokPosition >= 1:
                    prev_token = self.lang[self.tokPosition - 1]
                    if prev_token.get()['type'] == 'IDENTIFIER' and prev_token.get()['val'] in memory:
                        class_name = prev_token.get()['val']
                        field_name = tok_val
                        
                        if self.next(by=1) == '=':
                            self.tokPosition += 2  # skip field name and '='
                            # Read value until semicolon
                            value_tokens = []
                            while self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] != 'SEMICOLON':
                                value_tokens.append(self.lang[self.tokPosition])
                                self.tokPosition += 1
                            if self.tokPosition < len(self.lang) and self.lang[self.tokPosition].get()['type'] == 'SEMICOLON':
                                self.tokPosition += 1  # skip ';'
                            else:
                                raise SyntaxError("Expected ';' after field definition")
                            
                            # Evaluate the value
                            parsed_value = FieldAssignment.evaluate(value_tokens)
                            
                            # Set the field
                            setField(
                                ClassReference(self.currentClass),
                                field_name,
                                'public',  # modifier from context
                                ClassType(class_name),
                                parsed_value,
                                isStatic=self.states['STATIC'],
                                isFinal=self.states['FINAL']
                            )
                            self.clear(noClearMode=True, noClearInfo=True)
                            continue
                        elif self.next(by=1) == ';':
                            self.tokPosition += 2
                            setField(
                                ClassReference(self.currentClass),
                                field_name,
                                'public',
                                ClassType(class_name),
                                isStatic=self.states['STATIC'],
                                isFinal=self.states['FINAL']
                            )
                            self.clear(noClearMode=True, noClearInfo=True)
                            self.tokPosition += 1
                            continue
            elif tok_type in RETURN_TYPES and 'field_def' in self.mode and self.peek().get()['type'] == 'LBRACKET':
                modifier = 'default'
                i = self.tokPosition - 1
                while i >= 0:
                    t = self.lang[i]
                    if t.get()['type'] in ('NEWLINE', 'SEMICOLON', 'START_DECLARATION'):
                        break
                    if t.get()['type'] in ACCESS_MODIFIERS or t.get()['val'] in ['public', 'private', 'protected', 'default']:
                        modifier = t.get()['val']
                        break
                    i -= 1
                self.mode = []
                result = ArrayAssignment.parse(self.lang, self.tokPosition, tok_type)
                if result is None:
                    raise SyntaxError("Malformed array field declaration")
                var_name, arrayType, array_value, self.tokPosition = result
                setField(
                    ClassReference(self.currentClass), var_name, modifier,
                    PrimitiveArrayWrapper(arrayType), array_value,
                    isStatic=self.states['STATIC'], isFinal=self.states['FINAL']
                )
                self.clear(noClearMode=True, noClearInfo=True)
                continue
            elif tok_type == EvalTokens.TOKENS['('] and not is_constructor_call and self.before(2,'type') in (RETURN_TYPES + [EvalTokens.TOKENS['void']]): # METHOD
                if 'method_def' in self.mode and self.currentClass and not self.currentInterface:
                    # <modifier>, <static?>, <return_type>, <method_name> ( <...>
                    #       4         3          2             1          ^ WE ARE HERE  
                    self.mode = ['is_active_method_def', 'arg_def']
                    args = argsList(self.handleArgumentDefinition())
                    checkedExecs = self.handleThrowStmt(token, len(list(args.keys())))
                    del self.info['thisMethodArgs']
                    parseby = 1 if self.states['STATIC'] else 0
                    methodReturnType = parseTokenAsType(self.before(by=2), True)
                    search_pos = self.tokPosition - 3
                    methodModifier = 'default'
                    while search_pos >= 0:
                        t = self.lang[search_pos]
                        if t.get()['type'] in ACCESS_MODIFIERS or t.get()['val'] in ['public', 'private', 'protected', 'default']:
                            methodModifier = t.get()['val']
                            break
                        elif t.get()['type'] == 'NEWLINE' or t.get()['type'] == 'START_DECLARATION':
                            break
                        search_pos -= 1
                    
                    isValidModifier(methodModifier)

                    if methodReturnType is ClassType:
                        self.handleMethodDefinition(self.before(), methodModifier, ClassType(self.before(by=2)), self.states['STATIC'], args, checkedExecs)
                    else:
                        self.handleMethodDefinition(self.before(), methodModifier, methodReturnType, self.states['STATIC'], args, checkedExecs)
                    self.info['thisMethodName'] = self.before()
                    if self.info.get('thisMethodName') == ENTRY_METHOD_NAME:
                        if methodModifier != 'public':
                            raise Exception(f'The {ENTRY_METHOD_NAME} method must be public')
                        if self.before(by=2) != 'void':
                            raise Exception(f'The {ENTRY_METHOD_NAME} method must return void')
                        if len(args) > 1:
                            raise Exception(f'The {ENTRY_METHOD_NAME} method must not have more than 1 argument')
                    ENTRY['entryClass'] = self.currentClass
                elif self.currentInterface:
                    # Interface method definition
                    parseby = 1 if self.states['STATIC'] else 0
                    args = argsList(self.handleArgumentDefinition())
                    checkedExecs = self.handleThrowStmt(token, len(list(args.keys())))
                    methodReturnType = parseTokenAsType(self.before(by=2), True)
                    methodModifier = self.before(by=3+parseby)
                    if str(methodModifier).isspace():
                        methodModifier = 'default'
                    else:
                        isValidModifier(methodModifier)
                    
                    # Check if this method has a body by looking ahead for '{'
                    has_body = False
                    scan_pos = self.tokPosition + 1
                    while scan_pos < len(self.lang):
                        t = self.lang[scan_pos]
                        if t.get()['type'] == 'START_DECLARATION':
                            has_body = True
                            break
                        elif t.get()['type'] in ('SEMICOLON', 'END_DECLARATION'):
                            break
                        scan_pos += 1
                    
                    isAbstract = not has_body
                    isStatic = self.states['STATIC']
                    
                    setInterfaceMethod(self.currentInterface, self.before(), methodReturnType, methodModifier, args, checkedExecs, isAbstract, isStatic)
                    self.clear(noClearMode=True, noClearInfo=True)
                    
                    if has_body:
                        self.mode = ['is_active_method_def', 'method_body']
                        self.info['thisMethodName'] = self.before()
                    else:
                        self.mode = []
                        self.info['thisMethodName'] = self.before()
            elif tok_type == EvalTokens.TOKENS[')']:
                if self.info.get('parenStack', 0) == 0:
                    raise SyntaxError(f'Unexpected closing parenthesis at pos {token.getPos()["pos"]}')
                self.info['parenStack'] -= 1
            self.tokPosition += 1
    def handleFieldDefinition(self, _type: object, modifier: str, interface: bool):
        valueTokens = self.read(self.peek(3), ';')
        parsedValue = FieldAssignment.evaluate(valueTokens)
        # Convert
        converted = convertValue(parsedValue, _type)
        if not interface:
            setField(ClassReference(self.currentClass), self.next(), modifier, _type, converted, isStatic=self.states['STATIC'], isFinal=self.states['FINAL'])
        else:
            setInterfaceField(self.currentInterface, self.next(), _type, converted)
        self.clear(noClearMode=True, noClearInfo=True)
    def handleArgumentDefinition(self):
        open_paren_idx = self.tokPosition 
        close_paren_idx = matchingParen(self.lang, open_paren_idx)
        arg_tokens = self.lang[open_paren_idx + 1 : close_paren_idx]
        
        self.info['thisMethodArgs'] = {}
        
        if arg_tokens:
            # Parse arguments from tokens
            arg_parts = []
            current_part = []
            depth = 0
            
            for tok in arg_tokens:
                val = tok.get()['val']
                if val == '(':
                    depth += 1
                    current_part.append(val)
                elif val == ')':
                    depth -= 1
                    current_part.append(val)
                elif val == ',' and depth == 0:
                    arg_parts.append(' '.join(current_part).strip())
                    current_part = []
                else:
                    current_part.append(val)
            if current_part:
                arg_parts.append(' '.join(current_part).strip())
            
            for arg in arg_parts:
                if not arg:
                    continue
                
                if '[' in arg and ']' in arg:
                    type_part = arg.split('[')[0].strip()
                    name_part = arg.split(']')[1].strip() if ']' in arg else ''
                    
                    if not name_part:
                        parts = arg.split()
                        if len(parts) >= 2:
                            if '[' in parts[-2] and ']' in parts[-2]:
                                type_part = parts[-2]
                                name_part = parts[-1]
                            elif '[' in parts[0] and ']' in parts[0]:
                                type_part = parts[0]
                                name_part = parts[1] if len(parts) > 1 else ''
                    
                    if name_part:
                        base_type = type_part.split('[')[0].strip()
                        try:
                            if base_type in RETURN_TYPE_STR:
                                arr_type = parseTokenAsType(base_type)
                                arg_type = PrimitiveArrayWrapper(arr_type)
                            elif isClass(base_type):
                                arg_type = PrimitiveArrayWrapper(ClassType(base_type))
                            else:
                                raise RuntimeError(f'Could not resolve array type: {base_type}')
                        except Exception:
                            raise RuntimeError(f'Could not resolve array type: {base_type}')
                        
                        self.info['thisMethodArgs'][name_part] = arg_type
                        continue
                
                parts = arg.split(' ')
                if len(parts) != 2:
                    raise SyntaxError(f"Invalid argument definition: '{arg}'")
                
                arg_type_token, arg_name = parts
                
                try:
                    isValidReturnType(parseTokenAsType(arg_type_token))
                    arg_type = parseTokenAsType(arg_type_token)
                except ValueError:
                    if '[' in arg_type_token and ']' in arg_type_token:
                        base_type = arg_type_token.split('[')[0]
                        if base_type in RETURN_TYPE_STR or isClass(base_type):
                            arr_type = parseTokenAsType(base_type)
                            arg_type = PrimitiveArrayWrapper(arr_type)
                        else:
                            raise RuntimeError(f'Could not resolve array type: {arg_type_token}')
                    elif isClass(arg_type_token):
                        arg_type = ClassReference(arg_type_token)
                    else:
                        raise RuntimeError(f'Could not resolve the argument stream: {arg}')
                
                self.info['thisMethodArgs'][arg_name] = arg_type
        return self.info['thisMethodArgs']
    def handleThrowStmt(self, currentToken: Token, argSize: int): # <method_name> (<args>) throws? <exceptions...>
        argSize = len(self.read(currentToken, ')')) + 1
        startRead = self.tokPosition + argSize
        endRead = self.tokPosition + len(self.read(currentToken, '{'))
        parsed_as_str = [a.get()['val'] for a in self.lang[startRead:endRead]]
        if len(parsed_as_str) > 0 and parsed_as_str[0] != 'throws':
            return []
        exceptions = []
        for token in self.lang[startRead+1:endRead]: # Skip the 'throws'
            if token.get()['val'] == ',':
                continue
            exceptions.append(token.get()['val'])
        parsed = []
        for e in exceptions:
            thisExec = getattr(builtins, e, object)
            if not issubclass(thisExec, (Exception, Warning)):
                raise NameError(f'Invalid exception "{e}"')
            parsed.append(thisExec)
        return parsed
    def handleNullFieldDefinition(self, _type: object, modifier: str, interface: bool = False):
        if not interface:
            setField(ClassReference(self.currentClass), self.next(), modifier, _type, default_value_for_type(_type), isStatic=self.states['STATIC'], isFinal=self.states['FINAL'])
        else:
            setInterfaceField(self.currentInterface, self.next, _type, default_value_for_type(_type))
    def handleMethodDefinition(self, methodName: str, methodModifier: str, methodReturnType: object, isStatic: bool, methodArgs: dict, throws: list = []):
        createMethod(ClassReference(self.currentClass), methodName, methodModifier, methodReturnType, isStatic, argsList(methodArgs), throws)
        self.clear(noClearMode=True, noClearInfo=True)
    def clear(self, noClearMode: bool = False, noClearArgStack: bool = False, noClearInfo: bool = False):
        if not noClearMode:
            self.mode = []
        if not noClearArgStack:
            self.argumentStack = []
        if not noClearInfo:
            self.info = {}
        self.states = {
            'FINAL': False,
            'STATIC': False,
        }
    def handleMethodBodyContent(self, currentToken: Token):
        # Code is not evaluated until an actual method call is made, so no need to parse or evaluate code for now.
        startArgRead = currentToken.getPos()['pos'] + 1
        openIndex = self.tokPosition
        depth = 1
        closeIndex = openIndex + 1
        while closeIndex < len(self.lang):
            t = self.lang[closeIndex].get()['type']
            if t == EvalTokens.TOKENS['{']:
                depth += 1
            elif t == EvalTokens.TOKENS['}']:
                depth -= 1
                if depth == 0:
                    break
            closeIndex += 1
        if depth != 0:
            raise SyntaxError(f"Unterminated method body starting at {currentToken.getPos()}")
        endArgRead = self.lang[closeIndex].getPos()['pos']
        methodBody = self.langParse.getSource()[startArgRead:endArgRead]
        if not self.currentInterface:
            if self.info['thisMethodName'] in memory[self.currentClass]['methods']:
                memory[self.currentClass]['methods'][self.info['thisMethodName']]['body'] = methodBody.strip()
                memory[self.currentClass]['methods'][self.info['thisMethodName']]['abstract'] = False
        else:
            interfaces[self.currentInterface]['methods'][self.info['thisMethodName']]['body'] = methodBody.strip()
            interfaces[self.currentInterface]['methods'][self.info['thisMethodName']]['abstract'] = False
        holder = self.currentClass if not self.currentInterface else self.currentInterface
        hasCheckdAllExeceptions(holder, self.info['thisMethodName'], not not self.currentInterface)
        self.tokPosition = closeIndex - 1
def invokeMethod(className: str, methodName: str, args: list, caller: str, thisRef: 'ObjectReference | None' = None, startClass: str | None = None, isInterfaceMethod: bool = False) -> Returnable:
    if not isInterfaceMethod:
        if startClass is not None:
            lookup_class = startClass
        else:
            lookup_class = className
        found_method = None
        
        current = lookup_class
        while current is not None:
            if current in memory and methodName in memory[current].get('methods', {}):
                found_method = memory[current]['methods'][methodName]
                break
            if current in memory and 'super' in memory[current]:
                current = memory[current]['super'].getClass()
            else:
                current = None
        
        if found_method is None:
            raise NameError(f"Method '{methodName}' not found in class hierarchy starting from '{lookup_class}'")
        
        if found_method.get('static', False):
            if thisRef is not None:
                print(f"[WARNING]: Static method {methodName} called with instance reference")
            thisRef = None
        else:
            if thisRef is None:
                if callStack and callStack[-1].this is not None:
                    thisRef = callStack[-1].this
                else:
                    raise RuntimeError(f"Cannot call instance method '{methodName}' without 'this' reference")
        mInfo = memory[className]['methods'][methodName]
        mArgTypes = list(mInfo['args'].values())
        for mArgId in range(len(mArgTypes)):
            isConsistentTypes(args[mArgId], mArgTypes[mArgId])
            args[mArgId] = coerceValue(args[mArgId], mArgTypes[mArgId])
        pushFrame(methodName, className, thisRef or newObject(className), args)

        mModifier = mInfo['modifier']
        thisScope = perspectiveOfClass(caller, className)
        isAllowedAtThisScope(mModifier, thisScope)
    else:
        if not isInterface(className):
            raise NameError(f"Interface '{className}' is not defined")
        if methodName not in interfaces[className]['methods']:
            raise NameError(f"Method '{methodName}' not found in interface '{className}'")
        mInfo = interfaces[className]['methods'][methodName]
        
        if not mInfo.get('static', False):
            raise RuntimeError(f"Cannot call non-static method '{methodName}' from interface '{className}' in static context")
        
        mArgTypes = list(mInfo.get('args', {}).values())
        for mArgId in range(len(mArgTypes)):
            isConsistentTypes(args[mArgId], mArgTypes[mArgId])
            args[mArgId] = coerceValue(args[mArgId], mArgTypes[mArgId])
        
        pushFrame(methodName, className, None, args)
    try:
        m = Method()
        m.parse()
        m.execute()
        return currentFrame().returnValue
    finally:
        popFrame()

choice = 'Retry'
fileName = (input('Enter file name: ') + '.txt')
if fileName == '.txt':
    print('[INFO]: Defaulted file name to \'Test\'')
    fileName = 'Test.txt'
oldContent = ''
while choice == 'Retry':
    try:
        content = (Path(__file__).parent / fileName).read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f'[WARNING]: The file was not found in directory {Path(__file__).parent}')
        fileName = (input('Enter file name: ') + '.txt')
    os.system('cls')
    if oldContent == content:
        print(f'[WARNING]: File reloading did not detect any changes in file. Did you save the file {fileName}?')
    try:
        userArgs = input('[ENTER ARGS]: ')
        os.system('cls')
        Exec = Execution(Intepreter(content))
        print('CONSOLE OUTPUT:\n')
        Exec.executeTokens()
        try:
            if ENTRY['entryClass'] not in memory or ENTRY_METHOD_NAME not in memory[ENTRY['entryClass']]['methods']:
                raise NameError(f'An unexpected error occured while resolving "{ENTRY_METHOD_NAME}"')
            entryArgTypes = list(memory[ENTRY['entryClass']]['methods'][ENTRY_METHOD_NAME]['args'].values())
            callArgs = []
            if entryArgTypes:
                rawArgs = userArgs.split()
                argType = entryArgTypes[0]
                if isinstance(argType, PrimitiveArrayWrapper):
                    elementType = argType.getArrayType()
                    elements = [convertValue(String(a), elementType) for a in rawArgs]
                    callArgs = [newPrimitiveArray(elementType, len(elements), elements)]
                else:
                    callArgs = [convertValue(String(' '.join(rawArgs)), argType)]
            start = time.time()
            invokeMethod(ENTRY['entryClass'], ENTRY_METHOD_NAME, callArgs, caller=ENTRY['entryClass'])
            end = time.time()
            print(f'[INFO]: Program exited, with duration of {round(round(end-start, 6)*1000,2)}ms')
        except NameError:
            raise NameError(f'An unexpected error occured while resolving "{ENTRY_METHOD_NAME}"')
    except Exception as e:
        for line in traceback.format_exception(e)[1:-1]:
            print(line.strip())
        print(f'\n[ERROR]: {traceback.format_exception(e)[-1]}')
    choice = 'Retry' if not input('\n[ENTER]: Reload file [OTHER+ENTER]: Exit console') else ''
    if choice == 'Retry':
        Exec.reset()
    os.system('cls')
    oldContent = content
