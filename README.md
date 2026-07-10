# Python-JDK
P-JDK is an interesting replica of the popular Java programming language, created inside Python!

To play around with P-JDK and test its limits, just download the latest release in the Releases page. It does not require Python since it is bundled into an executable, created by PyInstaller. 
Of course, code made inside P-JDK are hundreds or even thousands of times slower than a real JDK. Obviously, this is not meant to be actually used, and rather a cool project.

# Supports
P-JDK supports all basic Java syntax (and fully replicates it), like defining classes:
```
public class Main { }
```
Defining methods:
```
public int add(int x, int y) { }
```
Creating statements:
```
if (a == 1) { }
```
Calling methods:
```
bob.sayHello();
alice.sayHello();
```
And much more!

# How to Use
To create a file that is readable by PJDK, create a simple text document in the SAME directory as the executable. Then, upon running the PJDK, type in the name of this file. 

All files must have a valid entry point, like a standard Java program:
```
public class Main{
  public static void main(){

  }
}
```
(Currently, there is no support for command line arguments (`String[] args`), so do not create that argument)

# Coming soon
These will be featured in next releases:
- Strings
- A simple print statement
- For loops
- Better error handling 

These are some issues that you may notice that may not be fixed in the next releases:
- Compiling messages may not match with actual issue
- 
