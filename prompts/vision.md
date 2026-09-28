## SOM
The screenshot shows a web page with numbered colored boxes drawn over interactive elements.
Which numbered box is the target: "{description}"?
Context (what the agent is trying to do): {goal}
Valid numbers: {ids}
Respond with JSON only: {{"id": <number or null if not visible>, "reason": "..."}}

## COORDS
The screenshot is {width}x{height} pixels of a web page.
Give the pixel coordinates of the CENTER of this target: "{description}".
Context (what the agent is trying to do): {goal}
If the target is not visible, return nulls.
Respond with JSON only: {{"x": <int or null>, "y": <int or null>, "reason": "..."}}
